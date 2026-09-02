"""Offline-only scoring of sealed ``real_live`` LLF run artifacts.

This module deliberately imports no transport, OpenAI SDK, HTTP, or network code. It
validates a complete sealed run against source-only inputs before opening the physically
isolated reference split, then produces a deterministic sealed report.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Annotated, Literal, cast

from pydantic import BaseModel, Field, StrictBool, StrictFloat, StrictInt, model_validator

from criteriabench.domain.schemas import StrictModel
from criteriabench.real.llf_semantics import (
    LlfMatchCounts,
    LlfSemanticComparison,
    LlfSemanticOutput,
    compare_llf_semantics,
    failed_llf_semantic_comparison,
    load_llf_scoring_references,
)
from criteriabench.real_eval.bootstrap import ClusterObservation, trial_cluster_interval
from criteriabench.real_eval.integrity import canonical_sha256
from criteriabench.real_eval.llf_binding import load_llf_generation_split
from criteriabench.real_eval.metrics import MatchCounts
from criteriabench.real_eval.models import (
    ClusterInterval,
    GenerationCase,
    GenerationDatasetBinding,
    MatchCountsModel,
)
from criteriabench.real_live.contracts import (
    MAX_OUTPUT_TOKENS,
    REQUEST_TIMEOUT_SECONDS,
    RESERVATION_PER_CASE_USD,
    AuthorizationClaim,
    AuthorizationConsumption,
    CanaryExecutionBinding,
    CaseOutcome,
    ExternalAttemptClaim,
    FailureKind,
    FrozenOutputContract,
    LivePlan,
    Money,
    PaidAuthorization,
    PendingAttempt,
    RunSummary,
    StrictOutputContract,
    UsageBreakdown,
    freeze_output_contract,
    llf_semantic_output_contract,
    money,
    parse_utc_timestamp,
    verify_execution_implementation,
)
from criteriabench.real_live.planning import (
    select_development_canary,
    verify_authorization,
    verify_execution_freshness,
    verify_plan_cases,
)

REPORT_SCHEMA_VERSION = "llf-live-score-report-v1"
EVALUATOR_ID = "criteriabench.real_eval.llf_live_score:v1"
BOOTSTRAP_RESAMPLES = 10_000
BOOTSTRAP_SEED = 7_291
MAX_ARTIFACT_BYTES = 20_000_000

HexDigest = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
CaseId = Annotated[str, Field(pattern=r"^NCT[0-9]{8}_[0-9]+$")]
TrialId = Annotated[str, Field(pattern=r"^NCT[0-9]{8}$")]
ScoreSplit = Literal["development", "test"]
SemanticStatus = Literal[
    "scored_completed",
    "scored_failure_as_empty",
    "operational_only_missing_reference",
]


class LlfLiveScoreError(ValueError):
    """A sealed artifact or scoring-boundary invariant failed closed."""


class LlfCaseMetrics(StrictModel):
    primary_structure: MatchCountsModel
    nodes: MatchCountsModel
    edges: MatchCountsModel
    calls: MatchCountsModel
    method_attributes: MatchCountsModel
    symbols: MatchCountsModel
    strings: MatchCountsModel
    booleans: MatchCountsModel
    typed_components: MatchCountsModel

    @model_validator(mode="after")
    def aggregates_match_components(self) -> LlfCaseMetrics:
        if _count_tuple(self.primary_structure) != _sum_count_tuples(self.nodes, self.edges):
            raise ValueError("primary structure must equal node plus edge counts")
        if _count_tuple(self.typed_components) != _sum_count_tuples(
            self.calls,
            self.method_attributes,
            self.symbols,
            self.strings,
            self.booleans,
        ):
            raise ValueError("typed components must equal their component counts")
        return self


class LlfCaseOperational(StrictModel):
    total_latency_ms: Annotated[StrictInt, Field(ge=0)] | None
    usage: UsageBreakdown
    charged_cost_usd: Money
    response_id_sha256: HexDigest | None
    provider_model: Annotated[str, Field(min_length=1, max_length=200)] | None
    provider_model_sha256: HexDigest | None
    provider_response_object: Annotated[str, Field(min_length=1, max_length=200)] | None
    provider_response_object_sha256: HexDigest | None
    provider_service_tier: Annotated[str, Field(min_length=1, max_length=200)] | None
    provider_service_tier_sha256: HexDigest | None

    @model_validator(mode="after")
    def charge_matches_usage_availability(self) -> LlfCaseOperational:
        expected = (
            money(RESERVATION_PER_CASE_USD)
            if self.usage.availability == "unavailable"
            else self.usage.total_cost_usd
        )
        if self.charged_cost_usd != expected:
            raise ValueError("reported case charge differs from sealed usage")
        return self


class LlfLiveCaseScore(StrictModel):
    ordinal: Annotated[StrictInt, Field(gt=0)]
    case_id: CaseId
    trial_id: TrialId
    source_sha256: HexDigest
    request_sha256: HexDigest
    attempt_sha256: HexDigest
    attempt_artifact_sha256: HexDigest
    external_attempt_claim_sha256: HexDigest
    external_attempt_claim_artifact_sha256: HexDigest
    outcome_sha256: HexDigest
    outcome_artifact_sha256: HexDigest
    attempt_started_at_utc: str
    outcome_finished_at_utc: str
    outcome_status: Literal["completed", "failed"]
    semantic_status: SemanticStatus
    failure_kind: FailureKind | None
    operational: LlfCaseOperational
    exact_match: StrictBool | None
    metrics: LlfCaseMetrics | None

    @model_validator(mode="after")
    def status_matches_metrics(self) -> LlfLiveCaseScore:
        if self.semantic_status == "operational_only_missing_reference":
            if self.exact_match is not None or self.metrics is not None:
                raise ValueError("missing-reference cases cannot carry semantic metrics")
        elif self.exact_match is None or self.metrics is None:
            raise ValueError("scorable cases require exact and component metrics")
        if self.outcome_status == "failed":
            if self.failure_kind is None:
                raise ValueError("failed outcomes require a failure kind")
            if self.semantic_status == "scored_completed" or self.exact_match is True:
                raise ValueError("failed outcomes cannot be completed semantic matches")
        elif self.failure_kind is not None:
            raise ValueError("completed outcomes cannot carry a failure kind")
        if self.semantic_status == "scored_failure_as_empty" and self.outcome_status != "failed":
            raise ValueError("empty failure scoring requires a failed outcome")
        if self.semantic_status == "scored_completed" and self.outcome_status != "completed":
            raise ValueError("completed semantic scoring requires a completed outcome")
        return self


class LlfMetricAggregate(StrictModel):
    semantic_case_count: Annotated[StrictInt, Field(gt=0)]
    exact_match_count: Annotated[StrictInt, Field(ge=0)]
    exact_match_accuracy: Annotated[
        StrictFloat,
        Field(ge=0.0, le=1.0, allow_inf_nan=False),
    ]
    primary_structure: MatchCountsModel
    nodes: MatchCountsModel
    edges: MatchCountsModel
    calls: MatchCountsModel
    method_attributes: MatchCountsModel
    symbols: MatchCountsModel
    strings: MatchCountsModel
    booleans: MatchCountsModel
    typed_components: MatchCountsModel

    @model_validator(mode="after")
    def aggregate_is_consistent(self) -> LlfMetricAggregate:
        if self.exact_match_count > self.semantic_case_count:
            raise ValueError("exact match count exceeds semantic denominator")
        if self.exact_match_accuracy != _six(
            _ratio(self.exact_match_count, self.semantic_case_count)
        ):
            raise ValueError("exact match accuracy does not match its count")
        if _count_tuple(self.primary_structure) != _sum_count_tuples(self.nodes, self.edges):
            raise ValueError("aggregate primary structure must equal nodes plus edges")
        if _count_tuple(self.typed_components) != _sum_count_tuples(
            self.calls,
            self.method_attributes,
            self.symbols,
            self.strings,
            self.booleans,
        ):
            raise ValueError("aggregate typed components do not reproduce")
        return self


class LlfUsageAggregate(StrictModel):
    case_count: Annotated[StrictInt, Field(gt=0)]
    usage_known_count: Annotated[StrictInt, Field(ge=0)]
    usage_unknown_count: Annotated[StrictInt, Field(ge=0)]
    input_tokens: Annotated[StrictInt, Field(ge=0)]
    uncached_input_tokens: Annotated[StrictInt, Field(ge=0)]
    cached_input_tokens: Annotated[StrictInt, Field(ge=0)]
    cache_write_input_tokens: Annotated[StrictInt, Field(ge=0)]
    output_tokens: Annotated[StrictInt, Field(ge=0)]
    uncached_input_cost_usd: Money
    cached_input_cost_usd: Money
    cache_write_input_cost_usd: Money
    output_cost_usd: Money
    known_total_cost_usd: Money
    charged_total_usd: Money
    budget_cap_usd: Money
    budget_utilization: Annotated[
        StrictFloat,
        Field(ge=0.0, le=1.0, allow_inf_nan=False),
    ]

    @model_validator(mode="after")
    def usage_totals_are_consistent(self) -> LlfUsageAggregate:
        if self.usage_known_count + self.usage_unknown_count != self.case_count:
            raise ValueError("usage availability counts must cover every case")
        if self.input_tokens != (
            self.uncached_input_tokens + self.cached_input_tokens + self.cache_write_input_tokens
        ):
            raise ValueError("aggregate input categories must equal input tokens")
        component_total = sum(
            (
                Decimal(value)
                for value in (
                    self.uncached_input_cost_usd,
                    self.cached_input_cost_usd,
                    self.cache_write_input_cost_usd,
                    self.output_cost_usd,
                )
            ),
            start=Decimal(0),
        )
        if self.known_total_cost_usd != money(component_total):
            raise ValueError("known usage cost does not equal its priced components")
        expected_utilization = _six(
            float(Decimal(self.charged_total_usd) / Decimal(self.budget_cap_usd))
        )
        if self.budget_utilization != expected_utilization:
            raise ValueError("budget utilization does not match charged total")
        return self


class LlfLatencyAggregate(StrictModel):
    complete_timing_count: Annotated[StrictInt, Field(ge=0)]
    observed_case_count: Annotated[StrictInt, Field(ge=0)]
    unobserved_case_count: Annotated[StrictInt, Field(ge=0)]
    total_latency_ms: Annotated[StrictInt, Field(ge=0)]
    p50_latency_ms: Annotated[StrictFloat, Field(ge=0.0, allow_inf_nan=False)] | None
    p95_latency_ms: Annotated[StrictFloat, Field(ge=0.0, allow_inf_nan=False)] | None

    @model_validator(mode="after")
    def percentiles_are_well_formed(self) -> LlfLatencyAggregate:
        if self.complete_timing_count != self.observed_case_count:
            raise ValueError("complete call timing must equal observed latency count")
        if self.observed_case_count == 0:
            if (
                self.total_latency_ms != 0
                or self.p50_latency_ms is not None
                or self.p95_latency_ms is not None
            ):
                raise ValueError("unobserved latency cannot carry totals or percentiles")
        elif self.p50_latency_ms is None or self.p95_latency_ms is None:
            raise ValueError("observed latency requires p50 and p95")
        elif self.p50_latency_ms > self.p95_latency_ms:
            raise ValueError("p50 latency cannot exceed p95 latency")
        return self


class LlfProviderAggregate(StrictModel):
    case_count: Annotated[StrictInt, Field(gt=0)]
    response_id_count: Annotated[StrictInt, Field(ge=0)]
    response_id_missing_count: Annotated[StrictInt, Field(ge=0)]
    unique_response_id_count: Annotated[StrictInt, Field(ge=0)]
    response_id_coverage: Annotated[
        StrictFloat,
        Field(ge=0.0, le=1.0, allow_inf_nan=False),
    ]
    provider_model_count: Annotated[StrictInt, Field(ge=0)]
    provider_model_missing_count: Annotated[StrictInt, Field(ge=0)]
    provider_model_counts: dict[str, Annotated[StrictInt, Field(gt=0)]]
    provider_model_sha256_count: Annotated[StrictInt, Field(ge=0)]
    provider_model_sha256_missing_count: Annotated[StrictInt, Field(ge=0)]
    provider_model_sha256_counts: dict[HexDigest, Annotated[StrictInt, Field(gt=0)]]
    provider_response_object_count: Annotated[StrictInt, Field(ge=0)]
    provider_response_object_missing_count: Annotated[StrictInt, Field(ge=0)]
    provider_response_object_counts: dict[str, Annotated[StrictInt, Field(gt=0)]]
    provider_response_object_sha256_count: Annotated[StrictInt, Field(ge=0)]
    provider_response_object_sha256_missing_count: Annotated[StrictInt, Field(ge=0)]
    provider_response_object_sha256_counts: dict[
        HexDigest,
        Annotated[StrictInt, Field(gt=0)],
    ]
    provider_service_tier_count: Annotated[StrictInt, Field(ge=0)]
    provider_service_tier_missing_count: Annotated[StrictInt, Field(ge=0)]
    provider_service_tier_counts: dict[str, Annotated[StrictInt, Field(gt=0)]]
    provider_service_tier_sha256_count: Annotated[StrictInt, Field(ge=0)]
    provider_service_tier_sha256_missing_count: Annotated[StrictInt, Field(ge=0)]
    provider_service_tier_sha256_counts: dict[
        HexDigest,
        Annotated[StrictInt, Field(gt=0)],
    ]

    @model_validator(mode="after")
    def provider_counts_are_consistent(self) -> LlfProviderAggregate:
        if self.response_id_count + self.response_id_missing_count != self.case_count:
            raise ValueError("response-ID counts must cover every case")
        if self.response_id_coverage != _six(_ratio(self.response_id_count, self.case_count)):
            raise ValueError("response-ID coverage does not match its count")
        if self.unique_response_id_count > self.response_id_count:
            raise ValueError("unique response-ID count exceeds present response IDs")
        if self.provider_model_count + self.provider_model_missing_count != self.case_count:
            raise ValueError("provider-model counts must cover every case")
        if sum(self.provider_model_counts.values()) != self.provider_model_count:
            raise ValueError("provider-model value counts do not reproduce")
        if (
            self.provider_model_sha256_count + self.provider_model_sha256_missing_count
            != self.case_count
        ):
            raise ValueError("provider-model hash counts must cover every case")
        if sum(self.provider_model_sha256_counts.values()) != self.provider_model_sha256_count:
            raise ValueError("provider-model hash value counts do not reproduce")
        if (
            self.provider_response_object_count + self.provider_response_object_missing_count
            != self.case_count
        ):
            raise ValueError("provider-object counts must cover every case")
        if (
            sum(self.provider_response_object_counts.values())
            != self.provider_response_object_count
        ):
            raise ValueError("provider-object value counts do not reproduce")
        if (
            self.provider_response_object_sha256_count
            + self.provider_response_object_sha256_missing_count
            != self.case_count
        ):
            raise ValueError("provider-object hash counts must cover every case")
        if (
            sum(self.provider_response_object_sha256_counts.values())
            != self.provider_response_object_sha256_count
        ):
            raise ValueError("provider-object hash value counts do not reproduce")
        if (
            self.provider_service_tier_count + self.provider_service_tier_missing_count
            != self.case_count
        ):
            raise ValueError("provider-service-tier counts must cover every case")
        if sum(self.provider_service_tier_counts.values()) != self.provider_service_tier_count:
            raise ValueError("provider-service-tier value counts do not reproduce")
        if (
            self.provider_service_tier_sha256_count
            + self.provider_service_tier_sha256_missing_count
            != self.case_count
        ):
            raise ValueError("provider-service-tier hash counts must cover every case")
        if (
            sum(self.provider_service_tier_sha256_counts.values())
            != self.provider_service_tier_sha256_count
        ):
            raise ValueError("provider-service-tier hash value counts do not reproduce")
        return self


class LlfOperationalSummary(StrictModel):
    plan_case_count: Annotated[StrictInt, Field(gt=0)]
    completed_count: Annotated[StrictInt, Field(ge=0)]
    failed_count: Annotated[StrictInt, Field(ge=0)]
    missing_reference_count: Annotated[StrictInt, Field(ge=0)]
    semantic_case_count: Annotated[StrictInt, Field(gt=0)]
    completion_rate: Annotated[
        StrictFloat,
        Field(ge=0.0, le=1.0, allow_inf_nan=False),
    ]
    failure_counts: dict[FailureKind, Annotated[StrictInt, Field(gt=0)]]
    usage: LlfUsageAggregate
    latency: LlfLatencyAggregate
    provider: LlfProviderAggregate

    @model_validator(mode="after")
    def counts_reproduce(self) -> LlfOperationalSummary:
        if self.completed_count + self.failed_count != self.plan_case_count:
            raise ValueError("operational statuses must cover every planned case")
        if self.semantic_case_count + self.missing_reference_count != self.plan_case_count:
            raise ValueError("semantic and missing-reference denominators must cover the plan")
        if sum(self.failure_counts.values()) != self.failed_count:
            raise ValueError("failure-kind counts must equal failed outcomes")
        if self.completion_rate != _six(_ratio(self.completed_count, self.plan_case_count)):
            raise ValueError("completion rate does not match completed outcomes")
        if self.usage.case_count != self.plan_case_count:
            raise ValueError("usage case count differs from the operational denominator")
        if (
            self.latency.observed_case_count + self.latency.unobserved_case_count
            != self.plan_case_count
        ):
            raise ValueError("latency counts differ from the operational denominator")
        if self.provider.case_count != self.plan_case_count:
            raise ValueError("provider case count differs from the operational denominator")
        return self


class LlfLiveInputBinding(StrictModel):
    preregistration_sha256: HexDigest
    preregistration_artifact_sha256: HexDigest
    execution_binding_sha256: HexDigest
    execution_binding_artifact_sha256: HexDigest
    plan_sha256: HexDigest
    plan_artifact_sha256: HexDigest
    summary_sha256: HexDigest
    summary_artifact_sha256: HexDigest
    authorization_sha256: HexDigest
    authorization_artifact_sha256: HexDigest
    authorization_claim_sha256: HexDigest
    authorization_claim_artifact_sha256: HexDigest
    authorization_consumption_sha256: HexDigest
    authorization_consumption_artifact_sha256: HexDigest
    external_attempt_claim_count: Annotated[StrictInt, Field(gt=0)]
    external_attempt_claim_inventory_sha256: HexDigest
    external_attempt_claim_artifact_inventory_sha256: HexDigest
    run_directory_sha256: HexDigest
    host_run_directory_sha256: HexDigest
    authorization_state_directory_sha256: HexDigest
    run_id: Annotated[str, Field(min_length=1, max_length=200)]
    runtime_image_id: Annotated[str, Field(pattern=r"^sha256:[0-9a-f]{64}$")]
    attempt_hash_set_sha256: HexDigest
    attempt_artifact_set_sha256: HexDigest
    outcome_hash_set_sha256: HexDigest
    outcome_artifact_set_sha256: HexDigest
    generation_dataset: GenerationDatasetBinding
    reference_artifact_sha256: HexDigest
    split_coverage_sha256: HexDigest
    output_contract: FrozenOutputContract
    execution_implementation_sha256: HexDigest
    execution_package_python_inventory_sha256: HexDigest
    evaluator_transitively_bound_by_package_inventory: Literal[True]


class LlfLiveScoreReportPayload(StrictModel):
    schema_version: Literal["llf-live-score-report-v1"]
    evaluator_id: Literal["criteriabench.real_eval.llf_live_score:v1"]
    evaluator_code_sha256: HexDigest
    purpose: Literal["development_llf_canary_25", "locked_llf_test"]
    split: ScoreSplit
    inputs: LlfLiveInputBinding
    operational: LlfOperationalSummary
    metrics: LlfMetricAggregate
    exact_match_trial_interval: ClusterInterval
    primary_structure_trial_interval: ClusterInterval
    cases: Annotated[tuple[LlfLiveCaseScore, ...], Field(min_length=1, max_length=1_800)]

    @model_validator(mode="after")
    def report_reproduces_from_cases(self) -> LlfLiveScoreReportPayload:
        if self.evaluator_code_sha256 != evaluator_code_sha256():
            raise ValueError("evaluator code hash differs from the current offline scorer")
        if [case.ordinal for case in self.cases] != list(range(1, len(self.cases) + 1)):
            raise ValueError("report case ordinals must be contiguous")
        if len({case.case_id for case in self.cases}) != len(self.cases):
            raise ValueError("report contains duplicate case IDs")
        completed = sum(case.outcome_status == "completed" for case in self.cases)
        failed = len(self.cases) - completed
        missing = sum(
            case.semantic_status == "operational_only_missing_reference" for case in self.cases
        )
        if (
            self.operational.plan_case_count,
            self.operational.completed_count,
            self.operational.failed_count,
            self.operational.missing_reference_count,
        ) != (len(self.cases), completed, failed, missing):
            raise ValueError("operational aggregate does not reproduce from cases")
        failure_counts = Counter(
            case.failure_kind for case in self.cases if case.failure_kind is not None
        )
        if dict(sorted(failure_counts.items())) != self.operational.failure_counts:
            raise ValueError("failure counts do not reproduce from cases")
        if (
            _operational_summary(
                self.cases,
                budget_cap_usd=self.operational.usage.budget_cap_usd,
            )
            != self.operational
        ):
            raise ValueError("operational economics do not reproduce from case artifacts")
        if _aggregate_case_scores(self.cases) != self.metrics:
            raise ValueError("metric aggregate does not reproduce from case scores")
        if self.inputs.attempt_hash_set_sha256 != canonical_sha256(
            [case.attempt_sha256 for case in self.cases]
        ):
            raise ValueError("attempt hash-set seal does not reproduce")
        if self.inputs.attempt_artifact_set_sha256 != canonical_sha256(
            [
                {"ordinal": case.ordinal, "sha256": case.attempt_artifact_sha256}
                for case in self.cases
            ]
        ):
            raise ValueError("attempt artifact-set seal does not reproduce")
        if self.inputs.external_attempt_claim_count != len(self.cases):
            raise ValueError("external attempt-claim count does not cover every case")
        if self.inputs.external_attempt_claim_inventory_sha256 != canonical_sha256(
            {
                "external_attempt_claim_hashes": tuple(
                    case.external_attempt_claim_sha256 for case in self.cases
                )
            }
        ):
            raise ValueError("external attempt-claim inventory seal does not reproduce")
        if self.inputs.external_attempt_claim_artifact_inventory_sha256 != canonical_sha256(
            [
                {
                    "ordinal": case.ordinal,
                    "sha256": case.external_attempt_claim_artifact_sha256,
                }
                for case in self.cases
            ]
        ):
            raise ValueError("external attempt-claim artifact inventory does not reproduce")
        if self.inputs.outcome_hash_set_sha256 != canonical_sha256(
            [case.outcome_sha256 for case in self.cases]
        ):
            raise ValueError("outcome hash-set seal does not reproduce")
        if self.inputs.outcome_artifact_set_sha256 != canonical_sha256(
            [
                {"ordinal": case.ordinal, "sha256": case.outcome_artifact_sha256}
                for case in self.cases
            ]
        ):
            raise ValueError("outcome artifact-set seal does not reproduce")
        if self.exact_match_trial_interval.estimate != self.metrics.exact_match_accuracy:
            raise ValueError("exact-match interval estimate differs from aggregate")
        if self.primary_structure_trial_interval.estimate != self.metrics.primary_structure.f1:
            raise ValueError("primary interval estimate differs from aggregate")
        return self


class LlfLiveScoreReport(LlfLiveScoreReportPayload):
    report_sha256: HexDigest

    @model_validator(mode="after")
    def seal_matches_payload(self) -> LlfLiveScoreReport:
        payload = self.model_dump(mode="json", exclude={"report_sha256"})
        if self.report_sha256 != canonical_sha256(payload):
            raise ValueError("report hash does not match its canonical payload")
        return self


@dataclass(frozen=True, slots=True)
class _Artifact[TModel: BaseModel]:
    model: TModel
    artifact_sha256: str


@dataclass(frozen=True, slots=True)
class _PublicCanaryChain:
    preregistration_sha256: str
    preregistration_artifact_sha256: str
    execution_binding: _Artifact[CanaryExecutionBinding]


@dataclass(frozen=True, slots=True)
class _LoadedRun:
    root: Path
    run_directory_sha256: str
    host_run_directory_sha256: str
    authorization_state_directory_sha256: str
    public_chain: _PublicCanaryChain
    plan: _Artifact[LivePlan]
    authorization: _Artifact[PaidAuthorization]
    authorization_claim: _Artifact[AuthorizationClaim]
    authorization_consumption: _Artifact[AuthorizationConsumption]
    summary: _Artifact[RunSummary]
    external_attempt_claims: tuple[_Artifact[ExternalAttemptClaim], ...]
    attempts: tuple[_Artifact[PendingAttempt], ...]
    outcomes: tuple[_Artifact[CaseOutcome], ...]


def evaluator_code_sha256() -> str:
    return hashlib.sha256(Path(__file__).read_bytes()).hexdigest()


def _load_public_canary_chain(
    *,
    preregistration_path: Path,
    execution_binding_path: Path,
    plan: _Artifact[LivePlan],
) -> _PublicCanaryChain:
    if plan.model.purpose != "development_llf_canary_25":
        raise LlfLiveScoreError("public canary-chain scorer rejects non-canary plans")
    # Lazy import avoids the preregistration module's intentional evaluator binding
    # creating a module-import cycle.
    from criteriabench.real_eval.llf_canary_preregistration import (
        load_execution_binding,
        load_preregistration,
        verify_canary_execution_binding,
    )

    preregistration_file = _standalone_regular_file(
        preregistration_path,
        label="public preregistration",
    )
    execution_binding_file = _standalone_regular_file(
        execution_binding_path,
        label="public execution binding",
    )
    try:
        preregistration = load_preregistration(preregistration_file)
        execution_binding = load_execution_binding(execution_binding_file)
        verify_canary_execution_binding(
            preregistration,
            execution_binding,
            plan.model,
            plan_artifact_sha256=plan.artifact_sha256,
        )
    except (OSError, ValueError) as error:
        raise LlfLiveScoreError(
            "public preregistration/execution binding does not reproduce"
        ) from error
    preregistration_artifact_sha256 = hashlib.sha256(preregistration_file.read_bytes()).hexdigest()
    execution_binding_artifact_sha256 = hashlib.sha256(
        execution_binding_file.read_bytes()
    ).hexdigest()
    if execution_binding.preregistration_artifact_sha256 != preregistration_artifact_sha256:
        raise LlfLiveScoreError("execution binding names different preregistration bytes")
    return _PublicCanaryChain(
        preregistration_sha256=preregistration.preregistration_sha256,
        preregistration_artifact_sha256=preregistration_artifact_sha256,
        execution_binding=_Artifact(
            model=execution_binding,
            artifact_sha256=execution_binding_artifact_sha256,
        ),
    )


def score_live_llf_run(
    *,
    run_dir: Path,
    authorization_state_dir: Path,
    preregistration_path: Path,
    execution_binding_path: Path,
    host_run_directory_sha256: str,
    authorization_state_directory_sha256: str,
    dataset_dir: Path,
    coverage_dir: Path,
) -> LlfLiveScoreReport:
    """Validate a complete live run, then score it against one physical LLF split."""

    loaded = _load_complete_run(
        run_dir,
        authorization_state_dir=authorization_state_dir,
        preregistration_path=preregistration_path,
        execution_binding_path=execution_binding_path,
        host_run_directory_sha256=host_run_directory_sha256,
        authorization_state_directory_sha256=(authorization_state_directory_sha256),
    )
    plan = loaded.plan.model
    summary = loaded.summary.model
    if plan.purpose == "development_llf_canary_25":
        split: ScoreSplit = "development"
    elif plan.purpose == "locked_llf_test":
        split = "test"
    else:
        raise LlfLiveScoreError("LLF scorer rejects GraphV2 product plans")

    generation = load_llf_generation_split(dataset_dir, split)
    if plan.source_dataset != generation.dataset:
        raise LlfLiveScoreError("plan source dataset differs from the sealed generation split")
    contract = llf_semantic_output_contract()
    if plan.output_contract != freeze_output_contract(contract):
        raise LlfLiveScoreError("plan LLF schema/parser/prompt contract is not current and exact")
    verify_execution_implementation(
        plan.execution_implementation,
        verify_installed_sdk=False,
    )
    expected_cases = (
        select_development_canary(generation.cases) if split == "development" else generation.cases
    )
    verify_plan_cases(plan, expected_cases)
    parsed_outputs = _validate_complete_run(
        loaded,
        expected_cases=expected_cases,
        contract=contract,
    )

    reference_corpus = load_llf_scoring_references(
        dataset_dir / f"{split}_references.jsonl",
        coverage_dir / f"llf-semantic-coverage-{split}.json",
        split=split,
    )
    reference_by_id = {reference.case_id: reference for reference in reference_corpus.references}
    missing_ids = set(reference_corpus.missing_upstream_case_ids)
    case_scores: list[LlfLiveCaseScore] = []
    observations: list[ClusterObservation] = []

    for external_claim_artifact, attempt_artifact, artifact, planned, case in zip(
        loaded.external_attempt_claims,
        loaded.attempts,
        loaded.outcomes,
        plan.cases,
        expected_cases,
        strict=True,
    ):
        outcome = artifact.model
        attempt = attempt_artifact.model
        external_claim = external_claim_artifact.model
        reference = reference_by_id.get(case.case_id)
        failure_kind = outcome.failure.kind if outcome.failure is not None else None
        case_operational = _case_operational(outcome)
        if reference is None:
            if case.case_id not in missing_ids:
                raise LlfLiveScoreError("planned case is absent from split scoring provenance")
            case_scores.append(
                LlfLiveCaseScore(
                    ordinal=planned.ordinal,
                    case_id=case.case_id,
                    trial_id=case.trial_id,
                    source_sha256=case.source_sha256,
                    request_sha256=outcome.request_sha256,
                    attempt_sha256=attempt.pending_sha256,
                    attempt_artifact_sha256=attempt_artifact.artifact_sha256,
                    external_attempt_claim_sha256=(external_claim.external_attempt_claim_sha256),
                    external_attempt_claim_artifact_sha256=(
                        external_claim_artifact.artifact_sha256
                    ),
                    outcome_sha256=outcome.outcome_sha256,
                    outcome_artifact_sha256=artifact.artifact_sha256,
                    attempt_started_at_utc=attempt.attempt_started_at_utc,
                    outcome_finished_at_utc=outcome.outcome_finished_at_utc,
                    outcome_status=outcome.status,
                    semantic_status="operational_only_missing_reference",
                    failure_kind=failure_kind,
                    operational=case_operational,
                    exact_match=None,
                    metrics=None,
                )
            )
            continue
        if reference.trial_id != case.trial_id or reference.source_sha256 != case.source_sha256:
            raise LlfLiveScoreError("reference identity or source hash differs from the plan")
        if outcome.status == "completed":
            comparison = compare_llf_semantics(
                parsed_outputs[planned.ordinal],
                reference.reference,
            )
            semantic_status: SemanticStatus = "scored_completed"
        else:
            comparison = failed_llf_semantic_comparison(reference.reference)
            semantic_status = "scored_failure_as_empty"
        metrics = _case_metrics(comparison)
        case_scores.append(
            LlfLiveCaseScore(
                ordinal=planned.ordinal,
                case_id=case.case_id,
                trial_id=case.trial_id,
                source_sha256=case.source_sha256,
                request_sha256=outcome.request_sha256,
                attempt_sha256=attempt.pending_sha256,
                attempt_artifact_sha256=attempt_artifact.artifact_sha256,
                external_attempt_claim_sha256=(external_claim.external_attempt_claim_sha256),
                external_attempt_claim_artifact_sha256=(external_claim_artifact.artifact_sha256),
                outcome_sha256=outcome.outcome_sha256,
                outcome_artifact_sha256=artifact.artifact_sha256,
                attempt_started_at_utc=attempt.attempt_started_at_utc,
                outcome_finished_at_utc=outcome.outcome_finished_at_utc,
                outcome_status=outcome.status,
                semantic_status=semantic_status,
                failure_kind=failure_kind,
                operational=case_operational,
                exact_match=comparison.exact_match,
                metrics=metrics,
            )
        )
        observations.append(
            ClusterObservation(
                case_id=case.case_id,
                trial_id=case.trial_id,
                ast_exact_match=comparison.exact_match,
                semantic_graph=_graph_counts(comparison.structure),
            )
        )

    scores = tuple(case_scores)
    aggregate = _aggregate_case_scores(scores)
    operational = _operational_summary(
        scores,
        budget_cap_usd=plan.budget_cap_usd,
    )
    exact_interval = trial_cluster_interval(
        observations,
        "ast_exact_match_accuracy",
        resamples=BOOTSTRAP_RESAMPLES,
        seed=BOOTSTRAP_SEED,
    )
    structure_interval = trial_cluster_interval(
        observations,
        "semantic_graph_f1",
        resamples=BOOTSTRAP_RESAMPLES,
        seed=BOOTSTRAP_SEED,
    )
    input_binding = LlfLiveInputBinding(
        preregistration_sha256=loaded.public_chain.preregistration_sha256,
        preregistration_artifact_sha256=(loaded.public_chain.preregistration_artifact_sha256),
        execution_binding_sha256=(
            loaded.public_chain.execution_binding.model.execution_binding_sha256
        ),
        execution_binding_artifact_sha256=(loaded.public_chain.execution_binding.artifact_sha256),
        plan_sha256=plan.plan_sha256,
        plan_artifact_sha256=loaded.plan.artifact_sha256,
        summary_sha256=summary.summary_sha256,
        summary_artifact_sha256=loaded.summary.artifact_sha256,
        authorization_sha256=loaded.authorization.model.authorization_sha256,
        authorization_artifact_sha256=loaded.authorization.artifact_sha256,
        authorization_claim_sha256=(loaded.authorization_claim.model.claim_sha256),
        authorization_claim_artifact_sha256=(loaded.authorization_claim.artifact_sha256),
        authorization_consumption_sha256=(
            loaded.authorization_consumption.model.consumption_sha256
        ),
        authorization_consumption_artifact_sha256=(
            loaded.authorization_consumption.artifact_sha256
        ),
        external_attempt_claim_count=len(loaded.external_attempt_claims),
        external_attempt_claim_inventory_sha256=(summary.external_attempt_claim_inventory_sha256),
        external_attempt_claim_artifact_inventory_sha256=canonical_sha256(
            [
                {
                    "ordinal": artifact.model.ordinal,
                    "sha256": artifact.artifact_sha256,
                }
                for artifact in loaded.external_attempt_claims
            ]
        ),
        run_directory_sha256=loaded.run_directory_sha256,
        host_run_directory_sha256=loaded.host_run_directory_sha256,
        authorization_state_directory_sha256=(loaded.authorization_state_directory_sha256),
        run_id=loaded.authorization.model.run_id,
        runtime_image_id=plan.runtime_image_id,
        attempt_hash_set_sha256=canonical_sha256(
            [artifact.model.pending_sha256 for artifact in loaded.attempts]
        ),
        attempt_artifact_set_sha256=canonical_sha256(
            [
                {"ordinal": artifact.model.ordinal, "sha256": artifact.artifact_sha256}
                for artifact in loaded.attempts
            ]
        ),
        outcome_hash_set_sha256=canonical_sha256(
            [artifact.model.outcome_sha256 for artifact in loaded.outcomes]
        ),
        outcome_artifact_set_sha256=canonical_sha256(
            [
                {"ordinal": artifact.model.ordinal, "sha256": artifact.artifact_sha256}
                for artifact in loaded.outcomes
            ]
        ),
        generation_dataset=generation.dataset,
        reference_artifact_sha256=reference_corpus.reference_artifact_sha256,
        split_coverage_sha256=reference_corpus.coverage_sha256,
        output_contract=plan.output_contract,
        execution_implementation_sha256=(plan.execution_implementation.implementation_sha256),
        execution_package_python_inventory_sha256=(
            plan.execution_implementation.package_python_inventory_sha256
        ),
        evaluator_transitively_bound_by_package_inventory=True,
    )
    payload = LlfLiveScoreReportPayload(
        schema_version=REPORT_SCHEMA_VERSION,
        evaluator_id=EVALUATOR_ID,
        evaluator_code_sha256=evaluator_code_sha256(),
        purpose=plan.purpose,
        split=split,
        inputs=input_binding,
        operational=operational,
        metrics=aggregate,
        exact_match_trial_interval=exact_interval,
        primary_structure_trial_interval=structure_interval,
        cases=scores,
    )
    body = payload.model_dump(mode="json")
    return LlfLiveScoreReport.model_validate({**body, "report_sha256": canonical_sha256(body)})


def _load_complete_run(
    run_dir: Path,
    *,
    authorization_state_dir: Path,
    preregistration_path: Path,
    execution_binding_path: Path,
    host_run_directory_sha256: str,
    authorization_state_directory_sha256: str,
) -> _LoadedRun:
    if run_dir.is_symlink():
        raise LlfLiveScoreError("run directory cannot be a symbolic link")
    root = run_dir.resolve(strict=True)
    if not root.is_dir():
        raise LlfLiveScoreError("run directory is not a directory")
    plan = _read_exact_model(_direct_file(root, "plan.json"), LivePlan)
    public_chain = _load_public_canary_chain(
        preregistration_path=preregistration_path,
        execution_binding_path=execution_binding_path,
        plan=plan,
    )
    binding = public_chain.execution_binding.model
    authorization = _read_exact_model(
        _direct_file(root, "authorization.json"),
        PaidAuthorization,
    )
    if authorization_state_dir.is_symlink():
        raise LlfLiveScoreError("authorization state directory cannot be a symbolic link")
    state_root = authorization_state_dir.resolve(strict=True)
    if not state_root.is_dir():
        raise LlfLiveScoreError("authorization state path is not a directory")
    authorization_claim = _read_exact_model(
        _direct_file(
            state_root,
            f"claim-{authorization.model.authorization_sha256}.json",
        ),
        AuthorizationClaim,
    )
    consumption = _read_exact_model(
        _direct_file(root, "authorization-consumed.json"),
        AuthorizationConsumption,
    )
    summary = _read_exact_model(_direct_file(root, "summary.json"), RunSummary)
    try:
        verify_authorization(
            plan.model,
            authorization.model,
            binding,
            preregistration_path=preregistration_path,
        )
    except ValueError as error:
        raise LlfLiveScoreError("authorization does not bind the public plan") from error
    path_identities = (
        binding.host_output_directory_sha256,
        authorization.model.host_run_directory_sha256,
        host_run_directory_sha256,
    )
    if len(set(path_identities)) != 1:
        raise LlfLiveScoreError("normalized host run-directory identity differs across chain")
    state_identities = (
        binding.authorization_state_directory_sha256,
        authorization.model.authorization_state_directory_sha256,
        authorization_state_directory_sha256,
    )
    if len(set(state_identities)) != 1:
        raise LlfLiveScoreError("authorization state-directory identity differs across chain")
    if authorization.model.run_directory_sha256 != binding.runtime_output_directory_sha256:
        raise LlfLiveScoreError("runtime output-directory identity differs across chain")
    expected_claim = {
        "schema_version": "real-live-authorization-claim-v1",
        "plan_sha256": plan.model.plan_sha256,
        "preregistration_sha256": public_chain.preregistration_sha256,
        "preregistration_artifact_sha256": public_chain.preregistration_artifact_sha256,
        "execution_binding_sha256": binding.execution_binding_sha256,
        "execution_binding_artifact_sha256": (public_chain.execution_binding.artifact_sha256),
        "authorization_sha256": authorization.model.authorization_sha256,
        "run_directory_sha256": authorization.model.run_directory_sha256,
        "host_run_directory_sha256": host_run_directory_sha256,
        "authorization_state_directory_sha256": authorization_state_directory_sha256,
        "authorization_claim_filename": (f"claim-{authorization.model.authorization_sha256}.json"),
        "run_id": authorization.model.run_id,
    }
    if (
        authorization_claim.model.model_dump(mode="json", exclude={"claim_sha256"})
        != expected_claim
    ):
        raise LlfLiveScoreError("external authorization claim differs from exact public chain")
    expected_consumption = {
        "schema_version": "real-live-authorization-consumption-v1",
        "plan_sha256": plan.model.plan_sha256,
        "authorization_sha256": authorization.model.authorization_sha256,
        "authorization_claim_sha256": authorization_claim.model.claim_sha256,
        "run_directory_sha256": authorization.model.run_directory_sha256,
        "run_id": authorization.model.run_id,
    }
    actual_consumption = consumption.model.model_dump(
        mode="json",
        exclude={"consumption_sha256"},
    )
    if actual_consumption != expected_consumption:
        raise LlfLiveScoreError("authorization consumption does not bind this exact run")
    if (
        summary.model.terminal_state != "completed"
        or summary.model.attempted_count != len(plan.model.cases)
        or summary.model.not_attempted_count != 0
    ):
        raise LlfLiveScoreError("scoring requires one terminal outcome per planned case")
    ordinals = range(1, len(plan.model.cases) + 1)
    expected_case_names = {f"case-{ordinal:04d}.json" for ordinal in ordinals}
    expected_attempt_names = {f"attempt-{ordinal:04d}.json" for ordinal in ordinals}
    required_names = {
        "plan.json",
        "authorization.json",
        "authorization-consumed.json",
        "summary.json",
        *expected_case_names,
        *expected_attempt_names,
    }
    entries = tuple(root.iterdir())
    if any(entry.is_symlink() or not entry.is_file() for entry in entries):
        raise LlfLiveScoreError("run directory contains a non-regular direct child")
    actual_names = {entry.name for entry in entries}
    optional_names = {".real-live.lock"}
    if required_names - actual_names or actual_names - required_names - optional_names:
        raise LlfLiveScoreError(
            "completed run directory has missing, pending, or conflicting artifacts"
        )
    attempts = tuple(
        _read_exact_model(_direct_file(root, name), PendingAttempt)
        for name in sorted(expected_attempt_names)
    )
    outcomes = tuple(
        _read_exact_model(_direct_file(root, name), CaseOutcome)
        for name in sorted(expected_case_names)
    )
    claim_prefix = f"attempt-{authorization.model.authorization_sha256}-"
    expected_external_names = {f"{claim_prefix}{ordinal:04d}.json" for ordinal in ordinals}
    actual_external_entries = tuple(
        entry for entry in state_root.iterdir() if entry.name.startswith(claim_prefix)
    )
    if any(entry.is_symlink() or not entry.is_file() for entry in actual_external_entries):
        raise LlfLiveScoreError("external attempt claim must be a regular direct child")
    actual_external_names = {entry.name for entry in actual_external_entries}
    if actual_external_names != expected_external_names:
        raise LlfLiveScoreError("external attempt-claim inventory is missing or has extras")
    external_attempt_claims = tuple(
        _read_exact_model(_direct_file(state_root, name), ExternalAttemptClaim)
        for name in sorted(expected_external_names)
    )
    previous_started_at: datetime | None = None
    for external_artifact, attempt_artifact, outcome_artifact, planned in zip(
        external_attempt_claims,
        attempts,
        outcomes,
        plan.model.cases,
        strict=True,
    ):
        external = external_artifact.model
        attempt = attempt_artifact.model
        if (
            attempt.plan_sha256,
            attempt.ordinal,
            attempt.case_id,
            attempt.source_sha256,
        ) != (
            plan.model.plan_sha256,
            planned.ordinal,
            planned.case_id,
            planned.source_sha256,
        ):
            raise LlfLiveScoreError("attempt ledger identity differs from the sealed plan")
        expected_external = {
            "schema_version": "real-live-external-attempt-claim-v1",
            "plan_sha256": plan.model.plan_sha256,
            "authorization_sha256": authorization.model.authorization_sha256,
            "authorization_claim_sha256": authorization_claim.model.claim_sha256,
            "preregistration_sha256": public_chain.preregistration_sha256,
            "execution_binding_sha256": binding.execution_binding_sha256,
            "run_id": authorization.model.run_id,
            "host_run_directory_sha256": host_run_directory_sha256,
            "authorization_state_directory_sha256": (authorization_state_directory_sha256),
            "ordinal": planned.ordinal,
            "pending": attempt.model_dump(mode="json"),
        }
        if (
            external.model_dump(mode="json", exclude={"external_attempt_claim_sha256"})
            != expected_external
        ):
            raise LlfLiveScoreError("external attempt claim differs from local exact attempt")
        outcome = outcome_artifact.model
        expected_outcome_identity = (
            plan.model.plan_sha256,
            planned.ordinal,
            planned.case_id,
            planned.trial_id,
            planned.document_id,
            planned.source_sha256,
            attempt.request_sha256,
            attempt.pending_sha256,
            external.external_attempt_claim_sha256,
        )
        actual_outcome_identity = (
            outcome.plan_sha256,
            outcome.ordinal,
            outcome.case_id,
            outcome.trial_id,
            outcome.document_id,
            outcome.source_sha256,
            outcome.request_sha256,
            outcome.attempt_sha256,
            outcome.external_attempt_claim_sha256,
        )
        if actual_outcome_identity != expected_outcome_identity:
            raise LlfLiveScoreError("outcome differs from plan, attempt, or external claim")
        started_at = parse_utc_timestamp(attempt.attempt_started_at_utc)
        try:
            verify_execution_freshness(plan.model, authorization.model, now=started_at)
        except ValueError as error:
            raise LlfLiveScoreError("paid attempt began outside the exact live window") from error
        if previous_started_at is not None and started_at < previous_started_at:
            raise LlfLiveScoreError("paid-attempt timestamps are not nondecreasing")
        previous_started_at = started_at
        _verify_attempt_outcome_timing(attempt=attempt, outcome=outcome)
    expected_external_inventory = canonical_sha256(
        {
            "external_attempt_claim_hashes": tuple(
                artifact.model.external_attempt_claim_sha256 for artifact in external_attempt_claims
            )
        }
    )
    expected_summary = {
        "plan_sha256": plan.model.plan_sha256,
        "authorization_sha256": authorization.model.authorization_sha256,
        "authorization_claim_sha256": authorization_claim.model.claim_sha256,
        "preregistration_sha256": public_chain.preregistration_sha256,
        "preregistration_artifact_sha256": public_chain.preregistration_artifact_sha256,
        "execution_binding_sha256": binding.execution_binding_sha256,
        "execution_binding_artifact_sha256": public_chain.execution_binding.artifact_sha256,
        "execution_implementation_sha256": (
            plan.model.execution_implementation.implementation_sha256
        ),
        "external_attempt_claim_count": len(external_attempt_claims),
        "external_attempt_claim_inventory_sha256": expected_external_inventory,
        "outcome_hashes": [artifact.model.outcome_sha256 for artifact in outcomes],
    }
    if summary.model.model_dump(mode="json", include=set(expected_summary)) != expected_summary:
        raise LlfLiveScoreError("summary does not bind the exact public and attempt ledger")
    return _LoadedRun(
        root=root,
        run_directory_sha256=authorization.model.run_directory_sha256,
        host_run_directory_sha256=host_run_directory_sha256,
        authorization_state_directory_sha256=authorization_state_directory_sha256,
        public_chain=public_chain,
        plan=plan,
        authorization=authorization,
        authorization_claim=authorization_claim,
        authorization_consumption=consumption,
        summary=summary,
        external_attempt_claims=external_attempt_claims,
        attempts=attempts,
        outcomes=outcomes,
    )


def _verify_attempt_outcome_timing(
    *,
    attempt: PendingAttempt,
    outcome: CaseOutcome,
) -> None:
    started_at = parse_utc_timestamp(attempt.attempt_started_at_utc)
    finished_at = parse_utc_timestamp(outcome.outcome_finished_at_utc)
    if finished_at < started_at:
        raise LlfLiveScoreError("case outcome predates its paid attempt")
    if outcome.failure is not None and outcome.failure.kind == "interrupted_unknown":
        if outcome.total_latency_ms is not None:
            raise LlfLiveScoreError("interrupted outcome cannot claim an observed latency")
        return
    if outcome.total_latency_ms is None:
        raise LlfLiveScoreError("provider outcome is missing its observed latency")
    timestamp_elapsed_ms = round((finished_at - started_at).total_seconds() * 1_000)
    tolerance_ms = 2_000
    if outcome.total_latency_ms > round(REQUEST_TIMEOUT_SECONDS * 1_000) + tolerance_ms:
        raise LlfLiveScoreError("provider outcome exceeds the whole-call timeout boundary")
    if abs(timestamp_elapsed_ms - outcome.total_latency_ms) > tolerance_ms:
        raise LlfLiveScoreError("provider latency conflicts with sealed UTC timestamps")


def _validate_complete_run(
    loaded: _LoadedRun,
    *,
    expected_cases: Sequence[GenerationCase],
    contract: StrictOutputContract[LlfSemanticOutput],
) -> dict[int, LlfSemanticOutput]:
    plan = loaded.plan.model
    summary = loaded.summary.model
    outcomes = tuple(artifact.model for artifact in loaded.outcomes)
    if len(outcomes) != len(plan.cases) or len(expected_cases) != len(plan.cases):
        raise LlfLiveScoreError("outcome, source, and plan counts differ")
    parsed_outputs: dict[int, LlfSemanticOutput] = {}
    for outcome, planned, case in zip(
        outcomes,
        plan.cases,
        expected_cases,
        strict=True,
    ):
        expected_identity = (
            planned.ordinal,
            planned.case_id,
            planned.trial_id,
            planned.document_id,
            planned.source_sha256,
        )
        actual_identity = (
            outcome.ordinal,
            outcome.case_id,
            outcome.trial_id,
            outcome.document_id,
            outcome.source_sha256,
        )
        if outcome.plan_sha256 != plan.plan_sha256 or actual_identity != expected_identity:
            raise LlfLiveScoreError("outcome plan, ordinal, or source identity is invalid")
        if outcome.request_sha256 != _offline_request_sha256(case, contract, plan):
            raise LlfLiveScoreError("outcome request hash differs from the sealed request")
        if outcome.status == "completed":
            try:
                parsed_outputs[outcome.ordinal] = LlfSemanticOutput.model_validate_json(
                    json.dumps(
                        outcome.normalized_output,
                        ensure_ascii=False,
                        allow_nan=False,
                        sort_keys=True,
                        separators=(",", ":"),
                    )
                )
            except Exception as error:
                raise LlfLiveScoreError(
                    "completed outcome is not a valid identity-free LLF semantic output"
                ) from error

    observed_latencies = tuple(
        outcome.total_latency_ms for outcome in outcomes if outcome.total_latency_ms is not None
    )
    charged_total = sum(
        (Decimal(outcome.charged_cost_usd) for outcome in outcomes),
        start=Decimal(0),
    )
    expected_summary = {
        "plan_sha256": plan.plan_sha256,
        "authorization_sha256": loaded.authorization.model.authorization_sha256,
        "authorization_claim_sha256": loaded.authorization_claim.model.claim_sha256,
        "preregistration_sha256": loaded.public_chain.preregistration_sha256,
        "preregistration_artifact_sha256": (loaded.public_chain.preregistration_artifact_sha256),
        "execution_binding_sha256": (
            loaded.public_chain.execution_binding.model.execution_binding_sha256
        ),
        "execution_binding_artifact_sha256": (
            loaded.public_chain.execution_binding.artifact_sha256
        ),
        "execution_implementation_sha256": (plan.execution_implementation.implementation_sha256),
        "terminal_state": "completed",
        "abort_reason": None,
        "case_count": len(plan.cases),
        "attempted_count": len(outcomes),
        "not_attempted_count": 0,
        "completed_count": sum(outcome.status == "completed" for outcome in outcomes),
        "failed_count": sum(outcome.status == "failed" for outcome in outcomes),
        "usage_unknown_count": sum(
            outcome.usage.availability == "unavailable" for outcome in outcomes
        ),
        "observed_latency_case_count": len(observed_latencies),
        "total_latency_ms": sum(observed_latencies),
        "budget_cap_usd": plan.budget_cap_usd,
        "charged_total_usd": money(charged_total),
        "budget_breached": False,
        "external_attempt_claim_count": len(loaded.external_attempt_claims),
        "external_attempt_claim_inventory_sha256": canonical_sha256(
            {
                "external_attempt_claim_hashes": tuple(
                    artifact.model.external_attempt_claim_sha256
                    for artifact in loaded.external_attempt_claims
                )
            }
        ),
        "outcome_hashes": [outcome.outcome_sha256 for outcome in outcomes],
    }
    actual_summary = summary.model_dump(mode="json", include=set(expected_summary))
    if actual_summary != expected_summary:
        raise LlfLiveScoreError("summary fields do not reproduce from plan and outcomes")
    return parsed_outputs


def _offline_request_sha256(
    case: GenerationCase,
    contract: StrictOutputContract[LlfSemanticOutput],
    plan: LivePlan,
) -> str:
    provider_input = {
        "criterion_kind": case.criterion_kind.value,
        "criterion_text": case.source_text,
    }
    request: dict[str, object] = {
        "model": plan.luna.model,
        "instructions": contract.instructions,
        "input": json.dumps(
            provider_input,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ),
        "reasoning": {"effort": plan.luna.reasoning_effort},
        "text": {
            "format": {
                "type": "json_schema",
                "name": contract.schema_name,
                "schema": contract.schema(),
                "strict": True,
            }
        },
        "max_output_tokens": MAX_OUTPUT_TOKENS,
        "service_tier": plan.luna.service_tier,
        "tools": list(plan.luna.tools),
        "store": plan.luna.store,
    }
    return canonical_sha256(request)


def _case_operational(outcome: CaseOutcome) -> LlfCaseOperational:
    return LlfCaseOperational(
        total_latency_ms=outcome.total_latency_ms,
        usage=outcome.usage,
        charged_cost_usd=outcome.charged_cost_usd,
        response_id_sha256=outcome.response_id_sha256,
        provider_model=outcome.provider_model,
        provider_model_sha256=outcome.provider_model_sha256,
        provider_response_object=outcome.provider_response_object,
        provider_response_object_sha256=outcome.provider_response_object_sha256,
        provider_service_tier=outcome.provider_service_tier,
        provider_service_tier_sha256=outcome.provider_service_tier_sha256,
    )


def _operational_summary(
    cases: Sequence[LlfLiveCaseScore],
    *,
    budget_cap_usd: str,
) -> LlfOperationalSummary:
    if not cases:
        raise ValueError("operational summary requires at least one case")
    completed = sum(case.outcome_status == "completed" for case in cases)
    failed = len(cases) - completed
    missing = sum(case.semantic_status == "operational_only_missing_reference" for case in cases)
    failure_counts = Counter(case.failure_kind for case in cases if case.failure_kind is not None)
    usages = tuple(case.operational.usage for case in cases)
    known = sum(usage.availability == "complete" for usage in usages)
    charged_total = sum(
        (Decimal(case.operational.charged_cost_usd) for case in cases),
        start=Decimal(0),
    )
    cost_fields = (
        "uncached_input_cost_usd",
        "cached_input_cost_usd",
        "cache_write_input_cost_usd",
        "output_cost_usd",
    )
    cost_totals = {
        name: money(
            sum(
                (Decimal(getattr(usage, name)) for usage in usages),
                start=Decimal(0),
            )
        )
        for name in cost_fields
    }
    usage_summary = LlfUsageAggregate(
        case_count=len(cases),
        usage_known_count=known,
        usage_unknown_count=len(cases) - known,
        input_tokens=sum(usage.input_tokens for usage in usages),
        uncached_input_tokens=sum(usage.uncached_input_tokens for usage in usages),
        cached_input_tokens=sum(usage.cached_input_tokens for usage in usages),
        cache_write_input_tokens=sum(usage.cache_write_input_tokens for usage in usages),
        output_tokens=sum(usage.output_tokens for usage in usages),
        **cost_totals,
        known_total_cost_usd=money(
            sum(
                (Decimal(usage.total_cost_usd) for usage in usages),
                start=Decimal(0),
            )
        ),
        charged_total_usd=money(charged_total),
        budget_cap_usd=budget_cap_usd,
        budget_utilization=_six(float(charged_total / Decimal(budget_cap_usd))),
    )
    latencies = sorted(
        case.operational.total_latency_ms
        for case in cases
        if case.operational.total_latency_ms is not None
    )
    latency_summary = LlfLatencyAggregate(
        complete_timing_count=len(latencies),
        observed_case_count=len(latencies),
        unobserved_case_count=len(cases) - len(latencies),
        total_latency_ms=sum(latencies),
        p50_latency_ms=_percentile(latencies, 0.50) if latencies else None,
        p95_latency_ms=_percentile(latencies, 0.95) if latencies else None,
    )
    provider_models = Counter(
        case.operational.provider_model
        for case in cases
        if case.operational.provider_model is not None
    )
    provider_model_hashes = Counter(
        case.operational.provider_model_sha256
        for case in cases
        if case.operational.provider_model_sha256 is not None
    )
    provider_objects = Counter(
        case.operational.provider_response_object
        for case in cases
        if case.operational.provider_response_object is not None
    )
    provider_object_hashes = Counter(
        case.operational.provider_response_object_sha256
        for case in cases
        if case.operational.provider_response_object_sha256 is not None
    )
    provider_service_tiers = Counter(
        case.operational.provider_service_tier
        for case in cases
        if case.operational.provider_service_tier is not None
    )
    provider_service_tier_hashes = Counter(
        case.operational.provider_service_tier_sha256
        for case in cases
        if case.operational.provider_service_tier_sha256 is not None
    )
    response_ids = tuple(
        case.operational.response_id_sha256
        for case in cases
        if case.operational.response_id_sha256 is not None
    )
    response_id_count = len(response_ids)
    provider_summary = LlfProviderAggregate(
        case_count=len(cases),
        response_id_count=response_id_count,
        response_id_missing_count=len(cases) - response_id_count,
        unique_response_id_count=len(set(response_ids)),
        response_id_coverage=_six(_ratio(response_id_count, len(cases))),
        provider_model_count=sum(provider_models.values()),
        provider_model_missing_count=len(cases) - sum(provider_models.values()),
        provider_model_counts=dict(sorted(provider_models.items())),
        provider_model_sha256_count=sum(provider_model_hashes.values()),
        provider_model_sha256_missing_count=(len(cases) - sum(provider_model_hashes.values())),
        provider_model_sha256_counts=dict(sorted(provider_model_hashes.items())),
        provider_response_object_count=sum(provider_objects.values()),
        provider_response_object_missing_count=(len(cases) - sum(provider_objects.values())),
        provider_response_object_counts=dict(sorted(provider_objects.items())),
        provider_response_object_sha256_count=sum(provider_object_hashes.values()),
        provider_response_object_sha256_missing_count=(
            len(cases) - sum(provider_object_hashes.values())
        ),
        provider_response_object_sha256_counts=dict(sorted(provider_object_hashes.items())),
        provider_service_tier_count=sum(provider_service_tiers.values()),
        provider_service_tier_missing_count=(len(cases) - sum(provider_service_tiers.values())),
        provider_service_tier_counts=dict(sorted(provider_service_tiers.items())),
        provider_service_tier_sha256_count=sum(provider_service_tier_hashes.values()),
        provider_service_tier_sha256_missing_count=(
            len(cases) - sum(provider_service_tier_hashes.values())
        ),
        provider_service_tier_sha256_counts=dict(sorted(provider_service_tier_hashes.items())),
    )
    return LlfOperationalSummary(
        plan_case_count=len(cases),
        completed_count=completed,
        failed_count=failed,
        missing_reference_count=missing,
        semantic_case_count=len(cases) - missing,
        completion_rate=_six(_ratio(completed, len(cases))),
        failure_counts=dict(sorted(failure_counts.items())),
        usage=usage_summary,
        latency=latency_summary,
        provider=provider_summary,
    )


def _case_metrics(comparison: LlfSemanticComparison) -> LlfCaseMetrics:
    return LlfCaseMetrics(
        primary_structure=_metric(comparison.structure),
        nodes=_metric(comparison.nodes),
        edges=_metric(comparison.edges),
        calls=_metric(comparison.calls),
        method_attributes=_metric(comparison.method_attributes),
        symbols=_metric(comparison.symbols),
        strings=_metric(comparison.strings),
        booleans=_metric(comparison.booleans),
        typed_components=_metric(comparison.typed_components),
    )


def _aggregate_case_scores(cases: Sequence[LlfLiveCaseScore]) -> LlfMetricAggregate:
    scorable = tuple(case for case in cases if case.metrics is not None)
    if not scorable:
        raise ValueError("LLF score report requires at least one semantic reference")
    exact = sum(case.exact_match is True for case in scorable)
    metric_names = (
        "primary_structure",
        "nodes",
        "edges",
        "calls",
        "method_attributes",
        "symbols",
        "strings",
        "booleans",
        "typed_components",
    )
    aggregates: dict[str, MatchCountsModel] = {}
    for name in metric_names:
        counts = (0, 0, 0)
        for case in scorable:
            if case.metrics is None:
                raise AssertionError("scorable case unexpectedly lacks metrics")
            counts = cast(
                tuple[int, int, int],
                tuple(
                    left + right
                    for left, right in zip(
                        counts,
                        _count_tuple(getattr(case.metrics, name)),
                        strict=True,
                    )
                ),
            )
        aggregates[name] = _metric_from_tuple(counts)
    return LlfMetricAggregate(
        semantic_case_count=len(scorable),
        exact_match_count=exact,
        exact_match_accuracy=_six(_ratio(exact, len(scorable))),
        **aggregates,
    )


def _metric(counts: LlfMatchCounts) -> MatchCountsModel:
    return MatchCountsModel(
        true_positive=counts.true_positive,
        false_positive=counts.false_positive,
        false_negative=counts.false_negative,
        precision=_six(counts.precision),
        recall=_six(counts.recall),
        f1=_six(counts.f1),
    )


def _metric_from_tuple(counts: tuple[int, int, int]) -> MatchCountsModel:
    return _metric(LlfMatchCounts(*counts))


def _graph_counts(counts: LlfMatchCounts) -> MatchCounts:
    return MatchCounts(
        true_positive=counts.true_positive,
        false_positive=counts.false_positive,
        false_negative=counts.false_negative,
    )


def _count_tuple(value: MatchCountsModel) -> tuple[int, int, int]:
    return value.true_positive, value.false_positive, value.false_negative


def _sum_count_tuples(*values: MatchCountsModel) -> tuple[int, int, int]:
    return cast(
        tuple[int, int, int],
        tuple(sum(items) for items in zip(*(_count_tuple(value) for value in values), strict=True)),
    )


def _ratio(numerator: int, denominator: int) -> float:
    return numerator / denominator if denominator else 0.0


def _percentile(values: Sequence[int], probability: float) -> float:
    if not values:
        raise ValueError("percentile requires at least one value")
    position = (len(values) - 1) * probability
    lower = int(position)
    upper = min(lower + 1, len(values) - 1)
    fraction = position - lower
    return _six(values[lower] * (1.0 - fraction) + values[upper] * fraction)


def _six(value: float) -> float:
    return round(value, 6)


def _direct_file(root: Path, name: str) -> Path:
    if Path(name).name != name:
        raise LlfLiveScoreError("artifact name must be a direct child")
    candidate = root / name
    if candidate.is_symlink():
        raise LlfLiveScoreError(f"sealed artifact cannot be a symbolic link: {name}")
    path = candidate.resolve(strict=True)
    if path.parent != root or not path.is_file():
        raise LlfLiveScoreError(f"sealed artifact is not a direct regular file: {name}")
    return path


def _standalone_regular_file(path: Path, *, label: str) -> Path:
    if path.is_symlink():
        raise LlfLiveScoreError(f"{label} cannot be a symbolic link")
    resolved = path.resolve(strict=True)
    if not resolved.is_file():
        raise LlfLiveScoreError(f"{label} is not a regular file")
    return resolved


def _read_exact_model[TModel: BaseModel](
    path: Path,
    model_type: type[TModel],
) -> _Artifact[TModel]:
    raw = path.read_bytes()
    if len(raw) > MAX_ARTIFACT_BYTES:
        raise LlfLiveScoreError("sealed artifact exceeds the offline size limit")
    try:
        model = model_type.model_validate_json(raw)
    except Exception as error:
        raise LlfLiveScoreError(
            "sealed artifact fails its strict model or internal hash"
        ) from error
    if _model_bytes(model) != raw:
        raise LlfLiveScoreError("sealed artifact is not canonical JSON")
    return _Artifact(model=model, artifact_sha256=hashlib.sha256(raw).hexdigest())


def _model_bytes(model: BaseModel) -> bytes:
    return (
        json.dumps(
            model.model_dump(mode="json"),
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode("utf-8")


def report_bytes(report: LlfLiveScoreReport) -> bytes:
    """Return the canonical, newline-terminated report artifact."""

    return _model_bytes(report)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    for command in ("score", "check"):
        subparser = subparsers.add_parser(command)
        subparser.add_argument("--run-dir", type=Path, required=True)
        subparser.add_argument("--authorization-state-dir", type=Path, required=True)
        subparser.add_argument("--preregistration", type=Path, required=True)
        subparser.add_argument("--execution-binding", type=Path, required=True)
        subparser.add_argument("--host-run-directory-sha256", required=True)
        subparser.add_argument("--authorization-state-directory-sha256", required=True)
        subparser.add_argument("--dataset-dir", type=Path, required=True)
        subparser.add_argument("--coverage-dir", type=Path, required=True)
        if command == "score":
            subparser.add_argument("--output", type=Path, required=True)
        else:
            subparser.add_argument("--report", type=Path, required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    try:
        report = score_live_llf_run(
            run_dir=args.run_dir,
            authorization_state_dir=args.authorization_state_dir,
            preregistration_path=args.preregistration,
            execution_binding_path=args.execution_binding,
            host_run_directory_sha256=args.host_run_directory_sha256,
            authorization_state_directory_sha256=(args.authorization_state_directory_sha256),
            dataset_dir=args.dataset_dir,
            coverage_dir=args.coverage_dir,
        )
        payload = report_bytes(report)
        if args.command == "score":
            output = cast(Path, args.output)
            output.parent.resolve(strict=True)
            with output.open("xb") as file:
                file.write(payload)
                file.flush()
                os.fsync(file.fileno())
            print(f"sealed LLF score report: {report.report_sha256}")
        else:
            existing_path = cast(Path, args.report)
            existing = existing_path.read_bytes()
            if len(existing) > MAX_ARTIFACT_BYTES or existing != payload:
                raise LlfLiveScoreError("score report does not exactly reproduce offline")
            LlfLiveScoreReport.model_validate_json(existing)
            print(f"verified LLF score report: {report.report_sha256}")
    except (OSError, ValueError) as error:
        parser.error(str(error))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
