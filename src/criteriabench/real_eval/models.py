"""Strict provider-neutral artifacts for CriteriaBench Real v1."""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from collections.abc import Mapping, Sequence
from decimal import ROUND_HALF_UP, Decimal
from typing import Annotated, Literal, Self

from pydantic import Field, StrictBool, StrictFloat, StrictInt, model_validator

from criteriabench.domain.schemas import StrictModel
from criteriabench.real.graph_v2 import (
    CriterionKindV2,
    EligibilityGraphV2,
    canonical_graph_sha256,
)

HexDigest = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
Identifier = Annotated[str, Field(pattern=r"^[A-Za-z0-9]+(?:[._-][A-Za-z0-9]+)*$")]
NonEmptyText = Annotated[str, Field(min_length=1, max_length=2_000)]
UsdAmount = Annotated[str, Field(pattern=r"^(?:0|[1-9][0-9]*)\.[0-9]{9}$")]
UtcTimestamp = Annotated[
    str,
    Field(pattern=r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$"),
]
SplitName = Literal["development", "test", "external_test"]

PROTOCOL_SCHEMA_VERSION = "real-eval-protocol-v1"
BUNDLE_SCHEMA_VERSION = "real-prediction-bundle-v1"
REPORT_SCHEMA_VERSION = "real-score-report-v1"
REAL_GRAPH_SCORING_V1_SHA256 = "c3aa2d3f84509b8e5dc8d00819afda6c249c4ee9ef42823d44289cf7176b7b7b"
USD_QUANTUM = Decimal("0.000000001")
TOKENS_PER_MILLION = Decimal(1_000_000)


class DatasetBinding(StrictModel):
    dataset_id: Identifier
    dataset_version: NonEmptyText
    split: SplitName
    split_unit: Literal["trial_id"]
    manifest_sha256: HexDigest
    case_set_sha256: HexDigest
    case_count: Annotated[StrictInt, Field(gt=0)]
    scorable_case_count: Annotated[StrictInt, Field(ge=0)]

    @model_validator(mode="after")
    def scorable_count_fits_total(self) -> Self:
        if self.scorable_case_count > self.case_count:
            raise ValueError("scorable_case_count cannot exceed case_count")
        return self


class GenerationDatasetBinding(StrictModel):
    """Source-only generation provenance with no reference-availability metadata."""

    dataset_id: Identifier
    dataset_version: NonEmptyText
    split: Literal["development", "test"]
    split_unit: Literal["trial_id"]
    generation_manifest_sha256: HexDigest
    generation_cases_sha256: HexDigest
    split_assignments_sha256: HexDigest
    case_set_sha256: HexDigest
    case_count: Annotated[StrictInt, Field(gt=0)]


class FrozenProtocolPayload(StrictModel):
    schema_version: Literal["real-eval-protocol-v1"]
    protocol_id: Identifier
    dataset: DatasetBinding
    locked_test_reference_policy: Literal["offline_only_after_bundle_sealed"]
    failure_policy: Literal["zero_primary_metrics"]
    bootstrap_unit: Literal["trial_id"]
    bootstrap_resamples: Annotated[StrictInt, Field(ge=1)]
    bootstrap_seed: StrictInt


class FrozenProtocol(FrozenProtocolPayload):
    protocol_sha256: HexDigest

    @model_validator(mode="after")
    def protocol_hash_matches_payload(self) -> Self:
        payload = self.model_dump(mode="json", exclude={"protocol_sha256"})
        if self.protocol_sha256 != _canonical_object_sha256(payload):
            raise ValueError("protocol_sha256 does not match the canonical protocol payload")
        return self


class GenerationCase(StrictModel):
    """Sanitized model input; deliberately contains no reference annotation."""

    case_id: Identifier
    trial_id: NonEmptyText
    document_id: NonEmptyText
    criterion_kind: CriterionKindV2
    source_text: Annotated[str, Field(min_length=1, max_length=1_000_000)]
    source_sha256: HexDigest


class ReferenceCase(GenerationCase):
    split: SplitName
    reference_status: Literal["available", "missing_upstream"]
    reference_sha256: HexDigest | None
    reference: EligibilityGraphV2 | None

    @model_validator(mode="after")
    def reference_matches_status(self) -> Self:
        available = self.reference_status == "available"
        if available:
            if self.reference is None or self.reference_sha256 is None:
                raise ValueError("available references require both graph and graph hash")
        elif self.reference is not None or self.reference_sha256 is not None:
            raise ValueError("missing_upstream references cannot include a graph or graph hash")
        return self


class InferenceParameters(StrictModel):
    temperature: Annotated[StrictFloat, Field(ge=0.0, le=2.0, allow_inf_nan=False)] | None
    top_p: Annotated[StrictFloat, Field(gt=0.0, le=1.0, allow_inf_nan=False)] | None
    max_output_tokens: Annotated[StrictInt, Field(gt=0)]
    seed: StrictInt | None
    reasoning_effort: Literal["none", "minimal", "low", "medium", "high", "xhigh"] | None
    request_timeout_ms: Annotated[StrictInt, Field(gt=0)]
    maximum_attempts: Annotated[StrictInt, Field(gt=0)]


class TokenPricing(StrictModel):
    currency: Literal["USD"]
    pricing_id: NonEmptyText
    pricing_sha256: HexDigest
    input_usd_per_million_tokens: UsdAmount
    output_usd_per_million_tokens: UsdAmount
    rounding: Literal["usd_9dp_half_up"]

    @model_validator(mode="after")
    def pricing_hash_matches_snapshot(self) -> Self:
        snapshot = {
            "currency": self.currency,
            "input_usd_per_million_tokens": self.input_usd_per_million_tokens,
            "output_usd_per_million_tokens": self.output_usd_per_million_tokens,
            "pricing_id": self.pricing_id,
            "rounding": self.rounding,
        }
        if self.pricing_sha256 != _canonical_object_sha256(snapshot):
            raise ValueError("pricing_sha256 does not match the canonical pricing snapshot")
        return self


class RunProvenance(StrictModel):
    run_id: Identifier
    created_at_utc: UtcTimestamp
    provider: Identifier
    model: NonEmptyText
    deployment: NonEmptyText | None
    prompt_sha256: HexDigest
    output_schema_sha256: HexDigest
    code_sha256: HexDigest
    config_sha256: HexDigest
    protocol_sha256: HexDigest
    inference: InferenceParameters
    pricing: TokenPricing
    paid_inference: StrictBool
    network_used: StrictBool

    @model_validator(mode="after")
    def paid_inference_requires_network(self) -> Self:
        if self.paid_inference and not self.network_used:
            raise ValueError("paid inference must declare network_used=true")
        return self


class TokenCounts(StrictModel):
    input_tokens: Annotated[StrictInt, Field(ge=0)]
    output_tokens: Annotated[StrictInt, Field(ge=0)]
    total_tokens: Annotated[StrictInt, Field(ge=0)]

    @model_validator(mode="after")
    def total_is_exact(self) -> Self:
        if self.total_tokens != self.input_tokens + self.output_tokens:
            raise ValueError("total_tokens must equal input_tokens plus output_tokens")
        return self


class UsagePricedCost(StrictModel):
    input_cost_usd: UsdAmount
    output_cost_usd: UsdAmount
    total_cost_usd: UsdAmount

    @model_validator(mode="after")
    def total_is_exact(self) -> Self:
        if Decimal(self.total_cost_usd) != (
            Decimal(self.input_cost_usd) + Decimal(self.output_cost_usd)
        ):
            raise ValueError("total_cost_usd must equal input_cost_usd plus output_cost_usd")
        return self


class UsageAccounting(StrictModel):
    """All-attempt usage; incomplete totals are explicitly lower bounds."""

    attempt_scope: Literal["all_attempts_including_retries"]
    availability: Literal["complete", "partial", "unavailable"]
    total_attempts: Annotated[StrictInt, Field(gt=0)]
    observed_attempts: Annotated[StrictInt, Field(ge=0)]
    monetary_totals_are_lower_bounds: StrictBool
    tokens: TokenCounts
    cost: UsagePricedCost

    @model_validator(mode="after")
    def availability_matches_attempt_coverage(self) -> Self:
        if self.observed_attempts > self.total_attempts:
            raise ValueError("observed_attempts cannot exceed total_attempts")
        expected = (
            "unavailable"
            if self.observed_attempts == 0
            else "complete"
            if self.observed_attempts == self.total_attempts
            else "partial"
        )
        if self.availability != expected:
            raise ValueError("usage availability does not match attempt coverage")
        if self.monetary_totals_are_lower_bounds != (expected != "complete"):
            raise ValueError("usage lower-bound flag does not match attempt coverage")
        if expected == "unavailable" and (
            self.tokens.total_tokens != 0 or Decimal(self.cost.total_cost_usd) != 0
        ):
            raise ValueError("unavailable usage requires zero placeholders")
        return self


class CaseExecution(StrictModel):
    case_id: Identifier
    trial_id: NonEmptyText
    document_id: NonEmptyText
    source_sha256: HexDigest
    request_sha256: HexDigest
    total_latency_ms: Annotated[StrictInt, Field(ge=0)]
    usage: UsageAccounting


class CompletedPrediction(CaseExecution):
    status: Literal["completed"]
    raw_response_sha256: HexDigest
    graph_sha256: HexDigest
    prediction: EligibilityGraphV2


FailureKind = Literal[
    "authentication",
    "authorization",
    "content_filter",
    "evidence_validation",
    "invalid_json",
    "network",
    "provider_error",
    "rate_limit",
    "refusal",
    "schema_validation",
    "timeout",
    "truncated_output",
]


class FailureDetail(StrictModel):
    kind: FailureKind
    retryable: StrictBool
    message_sha256: HexDigest


class FailedPrediction(CaseExecution):
    status: Literal["failed"]
    failure: FailureDetail


CasePrediction = Annotated[
    CompletedPrediction | FailedPrediction,
    Field(discriminator="status"),
]


class PredictionBundlePayload(StrictModel):
    schema_version: Literal["real-prediction-bundle-v1"]
    dataset: DatasetBinding
    run: RunProvenance
    cases: Annotated[list[CasePrediction], Field(min_length=1)]

    @model_validator(mode="after")
    def identities_counts_and_costs_are_consistent(self) -> Self:
        if self.dataset.case_count != len(self.cases):
            raise ValueError("dataset case_count must equal prediction count")
        case_ids = [case.case_id for case in self.cases]
        if len(case_ids) != len(set(case_ids)):
            raise ValueError("prediction bundle contains duplicate case IDs")
        for case in self.cases:
            if isinstance(case, CompletedPrediction):
                graph = case.prediction
                if graph.criterion_id != case.case_id:
                    raise ValueError("prediction criterion ID does not match case ID")
                if graph.source.trial_id != case.trial_id:
                    raise ValueError("prediction trial ID does not match case trial ID")
                if graph.source.document_id != case.document_id:
                    raise ValueError("prediction document ID does not match case document ID")
                if graph.source.text_sha256 != case.source_sha256:
                    raise ValueError("prediction source hash does not match case source hash")
                if canonical_graph_sha256(graph) != case.graph_sha256:
                    raise ValueError("prediction graph hash does not match canonical graph bytes")
            if case.usage.total_attempts > self.run.inference.maximum_attempts:
                raise ValueError("case attempts exceed the frozen maximum_attempts")
            expected_input = price_tokens(
                case.usage.tokens.input_tokens,
                self.run.pricing.input_usd_per_million_tokens,
            )
            expected_output = price_tokens(
                case.usage.tokens.output_tokens,
                self.run.pricing.output_usd_per_million_tokens,
            )
            if Decimal(case.usage.cost.input_cost_usd) != expected_input:
                raise ValueError("input cost does not match observed tokens and pricing")
            if Decimal(case.usage.cost.output_cost_usd) != expected_output:
                raise ValueError("output cost does not match observed tokens and pricing")
            if not self.run.paid_inference and Decimal(case.usage.cost.total_cost_usd) != 0:
                raise ValueError("unpaid inference cannot declare a non-zero token cost")
        return self


class PredictionBundle(PredictionBundlePayload):
    bundle_sha256: HexDigest

    @model_validator(mode="after")
    def bundle_hash_matches_payload(self) -> Self:
        payload = self.model_dump(mode="json", exclude={"bundle_sha256"})
        if self.bundle_sha256 != _canonical_object_sha256(payload):
            raise ValueError("bundle_sha256 does not match the canonical bundle payload")
        return self


class MatchCountsModel(StrictModel):
    true_positive: Annotated[StrictInt, Field(ge=0)]
    false_positive: Annotated[StrictInt, Field(ge=0)]
    false_negative: Annotated[StrictInt, Field(ge=0)]
    precision: Annotated[StrictFloat, Field(ge=0.0, le=1.0, allow_inf_nan=False)]
    recall: Annotated[StrictFloat, Field(ge=0.0, le=1.0, allow_inf_nan=False)]
    f1: Annotated[StrictFloat, Field(ge=0.0, le=1.0, allow_inf_nan=False)]

    @model_validator(mode="after")
    def derived_rates_match_counts(self) -> Self:
        expected_precision = _six(
            _ratio(self.true_positive, self.true_positive + self.false_positive)
        )
        expected_recall = _six(_ratio(self.true_positive, self.true_positive + self.false_negative))
        denominator = 2 * self.true_positive + self.false_positive + self.false_negative
        expected_f1 = _six(_ratio(2 * self.true_positive, denominator))
        if (
            self.precision != expected_precision
            or self.recall != expected_recall
            or self.f1 != expected_f1
        ):
            raise ValueError("match rates do not agree with TP/FP/FN counts")
        return self


class CaseScore(StrictModel):
    case_id: Identifier
    trial_id: NonEmptyText
    status: Literal["completed", "failed", "unscorable"]
    failure_kind: FailureKind | None
    ast_exact_match: StrictBool | None
    semantic_graph: MatchCountsModel | None
    nodes: MatchCountsModel | None
    edges: MatchCountsModel | None
    predicates: MatchCountsModel | None
    concept_evidence: MatchCountsModel | None

    @model_validator(mode="after")
    def status_failure_and_metrics_are_consistent(self) -> Self:
        metric_values = (
            self.semantic_graph,
            self.nodes,
            self.edges,
            self.predicates,
            self.concept_evidence,
        )
        if self.status == "unscorable":
            if self.ast_exact_match is not None or any(item is not None for item in metric_values):
                raise ValueError("unscorable cases cannot carry quality metrics")
        else:
            if self.ast_exact_match is None or any(item is None for item in metric_values):
                raise ValueError("scorable cases require every quality metric")
            if self.semantic_graph is None or self.nodes is None or self.edges is None:
                raise ValueError("scorable cases require semantic, node, and edge metrics")
            semantic = _count_tuple(self.semantic_graph)
            node_edge = tuple(
                left + right
                for left, right in zip(
                    _count_tuple(self.nodes),
                    _count_tuple(self.edges),
                    strict=True,
                )
            )
            if semantic != node_edge:
                raise ValueError("case semantic graph counts must equal node plus edge counts")
        if self.status == "failed" and self.failure_kind is None:
            raise ValueError("failed cases require a failure_kind")
        if self.status == "completed" and self.failure_kind is not None:
            raise ValueError("completed cases cannot carry a failure_kind")
        return self


class MetricAggregate(StrictModel):
    scorable_case_count: Annotated[StrictInt, Field(ge=0)]
    ast_exact_match_count: Annotated[StrictInt, Field(ge=0)]
    ast_exact_match_accuracy: Annotated[StrictFloat, Field(ge=0.0, le=1.0, allow_inf_nan=False)]
    semantic_graph: MatchCountsModel
    nodes: MatchCountsModel
    edges: MatchCountsModel
    predicates: MatchCountsModel
    concept_evidence: MatchCountsModel

    @model_validator(mode="after")
    def aggregate_counts_and_rates_are_consistent(self) -> Self:
        if self.ast_exact_match_count > self.scorable_case_count:
            raise ValueError("AST exact-match count cannot exceed scorable case count")
        expected_accuracy = _six(_ratio(self.ast_exact_match_count, self.scorable_case_count))
        if self.ast_exact_match_accuracy != expected_accuracy:
            raise ValueError("AST exact-match accuracy does not match aggregate counts")
        semantic_counts = (
            self.semantic_graph.true_positive,
            self.semantic_graph.false_positive,
            self.semantic_graph.false_negative,
        )
        node_edge_counts = (
            self.nodes.true_positive + self.edges.true_positive,
            self.nodes.false_positive + self.edges.false_positive,
            self.nodes.false_negative + self.edges.false_negative,
        )
        if semantic_counts != node_edge_counts:
            raise ValueError("semantic graph counts must equal node plus edge counts")
        return self


class ClusterInterval(StrictModel):
    estimate: Annotated[StrictFloat, Field(ge=-1.0, le=1.0, allow_inf_nan=False)]
    low: Annotated[StrictFloat, Field(ge=-1.0, le=1.0, allow_inf_nan=False)]
    high: Annotated[StrictFloat, Field(ge=-1.0, le=1.0, allow_inf_nan=False)]
    confidence: Annotated[StrictFloat, Field(gt=0.0, lt=1.0, allow_inf_nan=False)]
    resamples: Annotated[StrictInt, Field(ge=1)]
    seed: StrictInt
    resampling_unit: Literal["trial_id"]
    cluster_count: Annotated[StrictInt, Field(gt=0)]

    @model_validator(mode="after")
    def interval_is_well_formed(self) -> Self:
        if self.confidence != 0.95:
            raise ValueError("Real v1 cluster intervals require 95% confidence")
        if self.low > self.high:
            raise ValueError("cluster interval lower bound cannot exceed upper bound")
        return self


class UsageSummary(StrictModel):
    case_count: Annotated[StrictInt, Field(gt=0)]
    complete_case_count: Annotated[StrictInt, Field(ge=0)]
    partial_case_count: Annotated[StrictInt, Field(ge=0)]
    unavailable_case_count: Annotated[StrictInt, Field(ge=0)]
    monetary_totals_are_lower_bounds: StrictBool
    tokens: TokenCounts
    cost: UsagePricedCost
    total_latency_ms: Annotated[StrictInt, Field(ge=0)]
    p50_latency_ms: Annotated[StrictFloat, Field(ge=0.0, allow_inf_nan=False)]
    p95_latency_ms: Annotated[StrictFloat, Field(ge=0.0, allow_inf_nan=False)]
    total_attempts: Annotated[StrictInt, Field(gt=0)]
    total_retries: Annotated[StrictInt, Field(ge=0)]

    @model_validator(mode="after")
    def summary_counts_and_totals_are_consistent(self) -> Self:
        availability_count = (
            self.complete_case_count + self.partial_case_count + self.unavailable_case_count
        )
        if availability_count != self.case_count:
            raise ValueError("usage availability counts must cover every case")
        expected_lower_bound = self.partial_case_count + self.unavailable_case_count > 0
        if self.monetary_totals_are_lower_bounds != expected_lower_bound:
            raise ValueError("usage lower-bound flag does not match availability counts")
        if self.total_attempts < self.case_count:
            raise ValueError("usage total_attempts cannot be less than case_count")
        if self.total_retries != self.total_attempts - self.case_count:
            raise ValueError("usage total_retries does not match attempt count")
        if self.p50_latency_ms > self.p95_latency_ms:
            raise ValueError("p50 latency cannot exceed p95 latency")
        return self


class PredictionScoreReport(StrictModel):
    schema_version: Literal["real-score-report-v1"]
    scoring_contract_sha256: HexDigest
    bundle_sha256: HexDigest
    dataset: DatasetBinding
    run: RunProvenance
    operational_case_count: Annotated[StrictInt, Field(gt=0)]
    scorable_case_count: Annotated[StrictInt, Field(ge=0)]
    unscorable_reference_count: Annotated[StrictInt, Field(ge=0)]
    completed_cases: Annotated[StrictInt, Field(ge=0)]
    failed_cases: Annotated[StrictInt, Field(ge=0)]
    completion_rate: Annotated[StrictFloat, Field(ge=0.0, le=1.0, allow_inf_nan=False)]
    failure_counts: dict[FailureKind, Annotated[StrictInt, Field(gt=0)]]
    primary_all_scorable: MetricAggregate
    completed_only_diagnostic: MetricAggregate | None
    trial_cluster_intervals: dict[str, ClusterInterval]
    usage: UsageSummary
    cases: Annotated[list[CaseScore], Field(min_length=1)]

    @model_validator(mode="after")
    def report_is_self_consistent(self) -> Self:
        if self.scoring_contract_sha256 != REAL_GRAPH_SCORING_V1_SHA256:
            raise ValueError("report scoring contract hash is not Real graph scoring v1")
        case_count = len(self.cases)
        if len({case.case_id for case in self.cases}) != case_count:
            raise ValueError("score report contains duplicate case IDs")
        unscorable = sum(case.status == "unscorable" for case in self.cases)
        scorable = case_count - unscorable
        failed = sum(case.failure_kind is not None for case in self.cases)
        completed = case_count - failed
        if self.operational_case_count != case_count or self.dataset.case_count != case_count:
            raise ValueError("report operational count does not match cases or dataset")
        if self.scorable_case_count != scorable:
            raise ValueError("report scorable count does not match case statuses")
        if self.unscorable_reference_count != unscorable:
            raise ValueError("report unscorable count does not match case statuses")
        if self.dataset.scorable_case_count != scorable:
            raise ValueError("report scorable count does not match dataset binding")
        if self.completed_cases != completed or self.failed_cases != failed:
            raise ValueError("report completion counts do not match case failures")
        if self.completion_rate != _six(_ratio(completed, case_count)):
            raise ValueError("report completion_rate does not match completion counts")
        actual_failures: Counter[str] = Counter(
            case.failure_kind for case in self.cases if case.failure_kind is not None
        )
        if dict(self.failure_counts) != dict(actual_failures):
            raise ValueError("report failure_counts do not match case failures")
        if self.primary_all_scorable.scorable_case_count != scorable:
            raise ValueError("primary aggregate does not cover all scorable cases")
        scorable_cases = tuple(case for case in self.cases if case.status != "unscorable")
        if not _aggregate_matches_cases(self.primary_all_scorable, scorable_cases):
            raise ValueError("primary aggregate does not match case-level scores")
        completed_scorable = sum(case.status == "completed" for case in self.cases)
        if completed_scorable == 0:
            if self.completed_only_diagnostic is not None:
                raise ValueError(
                    "completed-only aggregate must be null without scorable completions"
                )
        elif (
            self.completed_only_diagnostic is None
            or self.completed_only_diagnostic.scorable_case_count != completed_scorable
        ):
            raise ValueError("completed-only aggregate count is inconsistent")
        elif not _aggregate_matches_cases(
            self.completed_only_diagnostic,
            tuple(case for case in self.cases if case.status == "completed"),
        ):
            raise ValueError("completed-only aggregate does not match case-level scores")
        if self.usage.case_count != case_count:
            raise ValueError("usage summary does not cover every operational case")

        expected_interval_keys = (
            {"semantic_graph_f1", "ast_exact_match_accuracy"} if scorable else set()
        )
        if set(self.trial_cluster_intervals) != expected_interval_keys:
            raise ValueError("trial-cluster interval set does not match scorable cohort")
        if scorable:
            cluster_count = len(
                {case.trial_id for case in self.cases if case.status != "unscorable"}
            )
            intervals = tuple(self.trial_cluster_intervals.values())
            if any(interval.cluster_count != cluster_count for interval in intervals):
                raise ValueError("cluster intervals do not match the scorable trial count")
            if len({(interval.resamples, interval.seed) for interval in intervals}) != 1:
                raise ValueError("cluster intervals must use one frozen resampling configuration")
            if (
                self.trial_cluster_intervals["semantic_graph_f1"].estimate
                != self.primary_all_scorable.semantic_graph.f1
                or self.trial_cluster_intervals["ast_exact_match_accuracy"].estimate
                != self.primary_all_scorable.ast_exact_match_accuracy
            ):
                raise ValueError("cluster interval estimates do not match primary aggregates")
        return self


def price_tokens(token_count: int, usd_per_million_tokens: str) -> Decimal:
    return (Decimal(token_count) * Decimal(usd_per_million_tokens) / TOKENS_PER_MILLION).quantize(
        USD_QUANTUM, rounding=ROUND_HALF_UP
    )


def usd(value: Decimal) -> str:
    return format(value.quantize(USD_QUANTUM, rounding=ROUND_HALF_UP), ".9f")


def _canonical_object_sha256(value: Mapping[str, object]) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _ratio(numerator: int, denominator: int) -> float:
    return numerator / denominator if denominator else 0.0


def _six(value: float) -> float:
    return round(value, 6)


def _count_tuple(counts: MatchCountsModel) -> tuple[int, int, int]:
    return counts.true_positive, counts.false_positive, counts.false_negative


def _aggregate_matches_cases(
    aggregate: MetricAggregate,
    cases: Sequence[CaseScore],
) -> bool:
    if aggregate.scorable_case_count != len(cases):
        return False
    if aggregate.ast_exact_match_count != sum(case.ast_exact_match is True for case in cases):
        return False
    fields = ("semantic_graph", "nodes", "edges", "predicates", "concept_evidence")
    for field in fields:
        aggregate_counts = getattr(aggregate, field)
        case_counts = [getattr(case, field) for case in cases]
        if any(counts is None for counts in case_counts):
            return False
        totals = tuple(
            sum(_count_tuple(counts)[index] for counts in case_counts if counts is not None)
            for index in range(3)
        )
        if _count_tuple(aggregate_counts) != totals:
            return False
    return True
