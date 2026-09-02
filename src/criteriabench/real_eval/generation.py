"""Actual-generation boundary that cannot access references or scoring code."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from time import perf_counter
from typing import Annotated, Protocol

from pydantic import Field, StrictStr

from criteriabench.domain.schemas import StrictModel
from criteriabench.real.graph_v2 import (
    CriterionKindV2,
    EligibilityGraphV2,
    EvidenceValidationError,
    FlatGraphOutputV2,
    SourceDocument,
    canonical_graph_sha256,
    flat_graph_strict_json_schema,
    inflate_model_output,
    validate_evidence,
)
from criteriabench.real_eval.integrity import (
    canonical_sha256,
    case_set_sha256,
    seal_bundle,
    validate_generation_cases,
    verify_protocol,
)
from criteriabench.real_eval.models import (
    BUNDLE_SCHEMA_VERSION,
    CompletedPrediction,
    DatasetBinding,
    FailedPrediction,
    FailureDetail,
    FrozenProtocol,
    GenerationCase,
    PredictionBundle,
    PredictionBundlePayload,
    RunProvenance,
    TokenCounts,
    UsageAccounting,
    UsagePricedCost,
)

FLAT_GRAPH_OUTPUT_SCHEMA_SHA256 = canonical_sha256(flat_graph_strict_json_schema())


@dataclass(frozen=True, slots=True)
class BackendSuccess:
    output: FlatGraphOutputV2
    raw_response_sha256: str
    usage: UsageAccounting


@dataclass(frozen=True, slots=True)
class BackendFailure:
    failure: FailureDetail
    usage: UsageAccounting


BackendOutcome = BackendSuccess | BackendFailure


class ProviderRequest(StrictModel):
    """The complete model-visible payload: criterion kind and text, nothing else."""

    criterion_kind: CriterionKindV2
    criterion_text: Annotated[StrictStr, Field(min_length=1, max_length=1_000_000)]


class RealGenerationBackend(Protocol):
    """Narrow adapter implemented by live providers and deterministic test fakes."""

    name: str
    model: str

    async def generate(self, request: ProviderRequest) -> BackendOutcome: ...


async def generate_bundle(
    cases: Sequence[GenerationCase],
    *,
    dataset: DatasetBinding,
    protocol: FrozenProtocol,
    run: RunProvenance,
    backend: RealGenerationBackend,
) -> PredictionBundle:
    """Generate one immutable outcome per case without ever loading a reference label."""

    if any(type(case) is not GenerationCase for case in cases):
        raise ValueError("generation accepts exact source-only GenerationCase objects")
    verify_protocol(protocol)
    if dataset != protocol.dataset:
        raise ValueError("dataset binding does not match the frozen evaluation protocol")
    if run.protocol_sha256 != protocol.protocol_sha256:
        raise ValueError("run provenance does not match the frozen evaluation protocol")
    validate_generation_cases(cases)
    if dataset.case_count != len(cases):
        raise ValueError("dataset binding case count does not match generation inputs")
    if dataset.case_set_sha256 != case_set_sha256(cases):
        raise ValueError("dataset binding case-set hash does not match generation inputs")
    if backend.name != run.provider or backend.model != run.model:
        raise ValueError("backend identity does not match frozen run provenance")
    if run.output_schema_sha256 != FLAT_GRAPH_OUTPUT_SCHEMA_SHA256:
        raise ValueError("run output schema hash does not match FlatGraphOutputV2")

    predictions: list[CompletedPrediction | FailedPrediction] = []
    for case in cases:
        provider_request = ProviderRequest(
            criterion_kind=case.criterion_kind,
            criterion_text=case.source_text,
        )
        request_sha256 = canonical_sha256(
            {
                "case_id": case.case_id,
                "trial_id": case.trial_id,
                "document_id": case.document_id,
                "criterion_kind": case.criterion_kind.value,
                "source_text": case.source_text,
                "prompt_sha256": run.prompt_sha256,
                "output_schema_sha256": run.output_schema_sha256,
                "config_sha256": run.config_sha256,
            }
        )
        started = perf_counter()
        try:
            outcome = await backend.generate(provider_request)
        except Exception as error:
            latency_ms = _elapsed_ms(started)
            predictions.append(
                _failed_case(
                    case,
                    request_sha256=request_sha256,
                    latency_ms=latency_ms,
                    usage=_unavailable_usage(),
                    failure=FailureDetail(
                        kind="provider_error",
                        retryable=False,
                        message_sha256=canonical_sha256({"safe_error_class": type(error).__name__}),
                    ),
                )
            )
            continue

        latency_ms = _elapsed_ms(started)
        if not isinstance(outcome, (BackendSuccess, BackendFailure)):
            predictions.append(
                _failed_case(
                    case,
                    request_sha256=request_sha256,
                    latency_ms=latency_ms,
                    usage=_unavailable_usage(),
                    failure=_backend_contract_failure(type(outcome).__name__),
                )
            )
            continue
        if isinstance(outcome, BackendFailure):
            predictions.append(
                _failed_case(
                    case,
                    request_sha256=request_sha256,
                    latency_ms=latency_ms,
                    usage=outcome.usage,
                    failure=outcome.failure,
                )
            )
            continue

        output = outcome.output
        if not isinstance(output, FlatGraphOutputV2):
            predictions.append(
                _failed_case(
                    case,
                    request_sha256=request_sha256,
                    latency_ms=latency_ms,
                    usage=outcome.usage,
                    failure=_backend_contract_failure(type(output).__name__),
                )
            )
            continue
        try:
            graph = inflate_model_output(
                output,
                source=SourceDocument(
                    trial_id=case.trial_id,
                    document_id=case.document_id,
                    text_sha256=case.source_sha256,
                    text_length=len(case.source_text),
                    source_url=None,
                ),
                criterion_id=case.case_id,
                criterion_kind=case.criterion_kind,
            )
            _validate_graph_identity(graph, case)
            validate_evidence(graph, case.source_text)
            completed = CompletedPrediction(
                case_id=case.case_id,
                trial_id=case.trial_id,
                document_id=case.document_id,
                source_sha256=case.source_sha256,
                request_sha256=request_sha256,
                total_latency_ms=latency_ms,
                usage=outcome.usage,
                status="completed",
                raw_response_sha256=outcome.raw_response_sha256,
                graph_sha256=canonical_graph_sha256(graph),
                prediction=graph,
            )
        except EvidenceValidationError as error:
            predictions.append(
                _failed_case(
                    case,
                    request_sha256=request_sha256,
                    latency_ms=latency_ms,
                    usage=outcome.usage,
                    failure=FailureDetail(
                        kind="evidence_validation",
                        retryable=False,
                        message_sha256=canonical_sha256({"evidence_error": error.code}),
                    ),
                )
            )
            continue
        except Exception as error:
            predictions.append(
                _failed_case(
                    case,
                    request_sha256=request_sha256,
                    latency_ms=latency_ms,
                    usage=outcome.usage,
                    failure=FailureDetail(
                        kind="schema_validation",
                        retryable=False,
                        message_sha256=canonical_sha256({"safe_error_class": type(error).__name__}),
                    ),
                )
            )
            continue
        predictions.append(completed)

    return seal_bundle(
        PredictionBundlePayload(
            schema_version=BUNDLE_SCHEMA_VERSION,
            dataset=dataset,
            run=run,
            cases=predictions,
        )
    )


def _validate_graph_identity(graph: EligibilityGraphV2, case: GenerationCase) -> None:
    if graph.criterion_id != case.case_id:
        raise ValueError("generated criterion ID differs from frozen case ID")
    if graph.criterion_kind != case.criterion_kind:
        raise ValueError("generated criterion kind differs from frozen case kind")
    if graph.source.trial_id != case.trial_id:
        raise ValueError("generated trial ID differs from frozen trial ID")
    if graph.source.document_id != case.document_id:
        raise ValueError("generated document ID differs from frozen document ID")
    if graph.source.text_sha256 != case.source_sha256:
        raise ValueError("generated source hash differs from frozen source hash")


def _failed_case(
    case: GenerationCase,
    *,
    request_sha256: str,
    latency_ms: int,
    usage: UsageAccounting,
    failure: FailureDetail,
) -> FailedPrediction:
    return FailedPrediction(
        case_id=case.case_id,
        trial_id=case.trial_id,
        document_id=case.document_id,
        source_sha256=case.source_sha256,
        request_sha256=request_sha256,
        total_latency_ms=latency_ms,
        usage=usage,
        status="failed",
        failure=failure,
    )


def _unavailable_usage() -> UsageAccounting:
    zero_cost = UsagePricedCost(
        input_cost_usd="0.000000000",
        output_cost_usd="0.000000000",
        total_cost_usd="0.000000000",
    )
    return UsageAccounting(
        attempt_scope="all_attempts_including_retries",
        availability="unavailable",
        total_attempts=1,
        observed_attempts=0,
        monetary_totals_are_lower_bounds=True,
        tokens=TokenCounts(input_tokens=0, output_tokens=0, total_tokens=0),
        cost=zero_cost,
    )


def _backend_contract_failure(safe_type_name: str) -> FailureDetail:
    return FailureDetail(
        kind="provider_error",
        retryable=False,
        message_sha256=canonical_sha256({"safe_backend_value_type": safe_type_name}),
    )


def _elapsed_ms(started: float) -> int:
    return max(0, round((perf_counter() - started) * 1_000))
