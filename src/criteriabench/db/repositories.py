"""Small persistence repositories with no HTTP or provider dependencies."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import select

from criteriabench.db.models import EvaluationRun, ExtractionRun
from criteriabench.db.session import Database
from criteriabench.domain.schemas import ClinicalTrialEligibility, TrialDocument
from criteriabench.evaluation.metrics import EvaluationReport
from criteriabench.providers.base import ProviderResult


class RunRepository:
    def __init__(self, database: Database) -> None:
        self._database = database

    async def create_extraction(
        self,
        trial: TrialDocument,
        *,
        provider: str,
        model: str,
        status: str = "queued",
        run_id: str | None = None,
    ) -> ExtractionRun:
        run = ExtractionRun(
            id=run_id or str(uuid4()),
            trial_id=trial.trial_id,
            trial_title=trial.title,
            provider=provider,
            model=model,
            status=status,
            request_json=trial.model_dump(mode="json"),
        )
        async with self._database.session() as session:
            session.add(run)
        return run

    async def mark_running(self, run_id: str) -> None:
        async with self._database.session() as session:
            run = await session.get(ExtractionRun, run_id)
            if run is None:
                raise LookupError(run_id)
            run.status = "running"
            run.started_at = datetime.now(UTC)

    async def mark_completed(self, run_id: str, result: ProviderResult) -> ExtractionRun:
        async with self._database.session() as session:
            run = await session.get(ExtractionRun, run_id)
            if run is None:
                raise LookupError(run_id)
            run.status = "completed"
            run.result_json = result.extraction.model_dump(mode="json")
            run.input_tokens = result.usage.input_tokens
            run.output_tokens = result.usage.output_tokens
            run.latency_ms = result.latency_ms
            run.estimated_cost_usd = result.estimated_cost_usd
            run.completed_at = datetime.now(UTC)
        return run

    async def mark_failed(self, run_id: str, *, error_code: str) -> None:
        async with self._database.session() as session:
            run = await session.get(ExtractionRun, run_id)
            if run is None:
                return
            run.status = "failed"
            run.error_code = error_code
            run.completed_at = datetime.now(UTC)

    async def get_extraction(self, run_id: str) -> ExtractionRun | None:
        async with self._database.session() as session:
            return await session.get(ExtractionRun, run_id)

    async def list_extractions(self, *, limit: int = 50) -> list[ExtractionRun]:
        statement = select(ExtractionRun).order_by(ExtractionRun.created_at.desc()).limit(limit)
        async with self._database.session() as session:
            return list((await session.scalars(statement)).all())

    async def create_evaluation(
        self,
        *,
        prediction: ClinicalTrialEligibility,
        reference: ClinicalTrialEligibility,
        report: EvaluationReport,
        extraction_run_id: str | None = None,
    ) -> EvaluationRun:
        run = EvaluationRun(
            id=str(uuid4()),
            extraction_run_id=extraction_run_id,
            trial_id=prediction.trial_id,
            prediction_json=prediction.model_dump(mode="json"),
            reference_json=reference.model_dump(mode="json"),
            metrics_json=report.model_dump(mode="json"),
        )
        async with self._database.session() as session:
            session.add(run)
        return run
