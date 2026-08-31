"""Versioned HTTP routes with a deliberately mock-only extraction boundary."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, Request, Response, status
from pydantic import ValidationError

from criteriabench import __version__
from criteriabench.api.schemas import (
    BenchmarkCaseResult,
    BenchmarkRequest,
    BenchmarkResponse,
    EvaluationRequest,
    EvaluationResponse,
    ExtractionExecutionRequest,
    ExtractionResponse,
    RunResponse,
    ServiceInfo,
    UsageResponse,
)
from criteriabench.contracts import SCHEMA_VERSION, extraction_contract_hash
from criteriabench.db.models import ExtractionRun
from criteriabench.domain.schemas import ClinicalTrialEligibility
from criteriabench.evaluation.metrics import EvaluationReport, evaluate_extraction
from criteriabench.observability import EVALUATIONS, EXTRACTIONS
from criteriabench.providers.base import ProviderError, ProviderResult
from criteriabench.queue import ExtractionJob, QueueUnavailable
from criteriabench.services.extraction import BudgetExceeded


def build_api_router() -> APIRouter:
    router = APIRouter()

    @router.get("/info", response_model=ServiceInfo)
    async def info(request: Request) -> ServiceInfo:
        settings = request.app.state.settings
        provider = request.app.state.provider
        return ServiceInfo(
            name=settings.app_name,
            version=__version__,
            environment=settings.environment,
            provider=provider.name,
            model=provider.model,
            paid_calls_enabled=False,
            credential_configured=settings.key_is_configured,
            authorization_guard_usd=settings.live_run_budget_usd,
            input_cost_per_million_usd=settings.input_cost_per_million_usd,
            output_cost_per_million_usd=settings.output_cost_per_million_usd,
        )

    @router.post(
        "/extractions",
        response_model=ExtractionResponse,
        status_code=status.HTTP_200_OK,
        responses={202: {"description": "Mock extraction accepted by the worker queue"}},
    )
    async def extract(
        payload: ExtractionExecutionRequest,
        request: Request,
        response: Response,
    ) -> ExtractionResponse:
        service = request.app.state.extraction_service
        repository = request.app.state.repository
        provider = request.app.state.provider
        _require_mock_provider(provider.name)

        if payload.execution_mode == "async":
            run = await repository.create_extraction(
                payload.trial,
                provider=provider.name,
                model=provider.model,
                status="queued",
            )
            try:
                await request.app.state.queue.enqueue(
                    ExtractionJob(
                        run_id=run.id,
                        trial=payload.trial,
                        provider=provider.name,
                        model=provider.model,
                        schema_version=SCHEMA_VERSION,
                        contract_hash=extraction_contract_hash(
                            provider=provider.name,
                            model=provider.model,
                        ),
                    )
                )
            except QueueUnavailable:
                await repository.mark_failed(run.id, error_code="queue_unavailable")
                raise HTTPException(status_code=503, detail="work queue unavailable") from None
            response.status_code = status.HTTP_202_ACCEPTED
            return ExtractionResponse(
                run_id=run.id,
                status="queued",
                provider=provider.name,
                model=provider.model,
                result=None,
                usage=UsageResponse(input_tokens=0, output_tokens=0, total_tokens=0),
                latency_ms=None,
                estimated_cost_usd=0.0,
            )

        try:
            outcome = await service.execute(payload.trial, persist=payload.persist)
        except BudgetExceeded as exc:
            EXTRACTIONS.labels(provider.name, "budget_blocked").inc()
            raise HTTPException(status_code=429, detail=str(exc)) from None
        except ProviderError:
            raise HTTPException(status_code=502, detail="extraction provider failed") from None
        return _extraction_response(outcome.run_id, outcome.status, outcome.result)

    @router.post("/evaluations", response_model=EvaluationResponse)
    async def evaluate(payload: EvaluationRequest, request: Request) -> EvaluationResponse:
        repository = request.app.state.repository
        if payload.extraction_run_id is not None:
            extraction_run = await repository.get_extraction(payload.extraction_run_id)
            if extraction_run is None:
                raise HTTPException(status_code=422, detail="extraction_run_id does not exist")
            if extraction_run.trial_id != payload.prediction.trial_id:
                raise HTTPException(
                    status_code=422,
                    detail="extraction_run_id belongs to a different trial",
                )
            if extraction_run.status != "completed":
                raise HTTPException(
                    status_code=422,
                    detail="extraction_run_id is not completed",
                )
            if extraction_run.result_json is None:
                raise HTTPException(
                    status_code=422,
                    detail="extraction_run_id has no stored result",
                )
            try:
                stored_prediction = ClinicalTrialEligibility.model_validate(
                    extraction_run.result_json
                )
            except ValidationError:
                raise HTTPException(
                    status_code=422,
                    detail="extraction_run_id has an invalid stored result",
                ) from None
            if stored_prediction != payload.prediction:
                raise HTTPException(
                    status_code=422,
                    detail="prediction does not match the linked extraction result",
                )

        report = evaluate_extraction(payload.prediction, payload.reference)
        evaluation_id: str | None = None
        if payload.persist:
            run = await repository.create_evaluation(
                prediction=payload.prediction,
                reference=payload.reference,
                report=report,
                extraction_run_id=payload.extraction_run_id,
            )
            evaluation_id = run.id
        EVALUATIONS.labels(str(payload.persist).lower()).inc()
        return EvaluationResponse(
            evaluation_id=evaluation_id,
            trial_id=payload.prediction.trial_id,
            report=report,
        )

    @router.post("/benchmarks", response_model=BenchmarkResponse)
    async def benchmark(payload: BenchmarkRequest, request: Request) -> BenchmarkResponse:
        settings = request.app.state.settings
        provider = request.app.state.provider
        _require_mock_provider(provider.name)
        if len(payload.cases) > settings.max_batch_size:
            raise HTTPException(status_code=422, detail="batch exceeds configured maximum")

        service = request.app.state.extraction_service
        projected = sum(service.estimated_request_cost(case.trial) for case in payload.cases)
        budget = service.live_budget
        if budget.spent_usd + budget.reserved_usd + projected > budget.maximum_usd:
            raise HTTPException(status_code=429, detail="batch would exceed the live-run budget")

        results: list[BenchmarkCaseResult] = []
        reports: list[EvaluationReport] = []
        for case in payload.cases:
            try:
                outcome = await service.execute(case.trial, persist=payload.persist)
            except BudgetExceeded as exc:
                raise HTTPException(status_code=429, detail=str(exc)) from None
            except ProviderError:
                raise HTTPException(status_code=502, detail="extraction provider failed") from None
            report = None
            if case.reference is not None:
                report = evaluate_extraction(outcome.result.extraction, case.reference)
                reports.append(report)
                EVALUATIONS.labels(str(payload.persist).lower()).inc()
                if payload.persist:
                    await request.app.state.repository.create_evaluation(
                        prediction=outcome.result.extraction,
                        reference=case.reference,
                        report=report,
                        extraction_run_id=outcome.run_id,
                    )
            results.append(
                BenchmarkCaseResult(
                    run_id=outcome.run_id,
                    trial_id=case.trial.trial_id,
                    extraction=outcome.result.extraction,
                    evaluation=report,
                    latency_ms=outcome.result.latency_ms,
                    estimated_cost_usd=outcome.result.estimated_cost_usd,
                )
            )
        return BenchmarkResponse(
            cases=results,
            evaluated_cases=len(reports),
            mean_exact_match_f1=_mean(reports, "exact_match_f1"),
            mean_token_f1=_mean(reports, "token_f1"),
            total_latency_ms=round(sum(item.latency_ms for item in results), 3),
            total_estimated_cost_usd=round(
                sum(item.estimated_cost_usd for item in results),
                6,
            ),
        )

    @router.get("/runs", response_model=list[RunResponse])
    async def list_runs(
        request: Request,
        limit: int = Query(default=50, ge=1, le=100),
    ) -> list[RunResponse]:
        runs = await request.app.state.repository.list_extractions(limit=limit)
        return [_run_response(run) for run in runs]

    @router.get("/runs/{run_id}", response_model=RunResponse)
    async def get_run(run_id: str, request: Request) -> RunResponse:
        run = await request.app.state.repository.get_extraction(run_id)
        if run is None:
            raise HTTPException(status_code=404, detail="run not found")
        return _run_response(run)

    return router


def _require_mock_provider(provider_name: str) -> None:
    if provider_name != "mock":
        raise HTTPException(
            status_code=422,
            detail="paid providers are disabled in the HTTP API; use the guarded benchmark CLI",
        )


def _mean(reports: list[EvaluationReport], field: str) -> float | None:
    if not reports:
        return None
    return round(sum(float(getattr(report, field)) for report in reports) / len(reports), 6)


def _extraction_response(
    run_id: str | None,
    status_value: str,
    result: ProviderResult,
) -> ExtractionResponse:
    return ExtractionResponse(
        run_id=run_id,
        status=status_value,
        provider=result.provider,
        model=result.model,
        result=result.extraction,
        usage=UsageResponse(
            input_tokens=result.usage.input_tokens,
            output_tokens=result.usage.output_tokens,
            total_tokens=result.usage.total_tokens,
        ),
        latency_ms=result.latency_ms,
        estimated_cost_usd=result.estimated_cost_usd,
    )


def _run_response(run: ExtractionRun) -> RunResponse:
    result = (
        ClinicalTrialEligibility.model_validate(run.result_json)
        if run.result_json is not None
        else None
    )
    return RunResponse(
        run_id=run.id,
        trial_id=run.trial_id,
        trial_title=run.trial_title,
        provider=run.provider,
        model=run.model,
        status=run.status,
        result=result,
        error_code=run.error_code,
        usage=UsageResponse(
            input_tokens=run.input_tokens,
            output_tokens=run.output_tokens,
            total_tokens=run.input_tokens + run.output_tokens,
        ),
        latency_ms=run.latency_ms,
        estimated_cost_usd=run.estimated_cost_usd,
        created_at=run.created_at,
        started_at=run.started_at,
        completed_at=run.completed_at,
    )
