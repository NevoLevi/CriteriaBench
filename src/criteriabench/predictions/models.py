"""Strict, provider-neutral contracts for frozen prediction artifacts."""

from __future__ import annotations

import hashlib
import json
from decimal import ROUND_HALF_UP, Decimal
from typing import Annotated, Literal

from pydantic import Field, StrictBool, StrictFloat, StrictInt, model_validator

from criteriabench.domain.schemas import ClinicalTrialEligibility, StrictModel
from criteriabench.suite.models import ErrorTaxonomySummary, MetricAggregate

HexDigest = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
Identifier = Annotated[str, Field(pattern=r"^[a-z0-9]+(?:[._-][a-z0-9]+)*$")]
NonEmptyText = Annotated[str, Field(min_length=1, max_length=300)]
UsdAmount = Annotated[str, Field(pattern=r"^(?:0|[1-9][0-9]*)\.[0-9]{9}$")]
UtcTimestamp = Annotated[
    str,
    Field(pattern=r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$"),
]

BUNDLE_SCHEMA_VERSION = "prediction-bundle-v1"
REPORT_SCHEMA_VERSION = "prediction-score-v1"
FIXTURE_CONTRACT_VERSION = "synthetic-v0.1-fixture-v1"
CURRENT_OUTPUT_SCHEMA_ID = "criteriabench.clinical_trial_eligibility.v1"
ZERO_USD = "0.000000000"
_USD_QUANTUM = Decimal("0.000000001")
_TOKENS_PER_MILLION = Decimal(1_000_000)


class DatasetBinding(StrictModel):
    """Immutable identity of the exact suite scored by a prediction run."""

    dataset_version: Identifier
    fixture_contract: Literal["synthetic-v0.1-fixture-v1"]
    manifest_name: Literal["manifest.json"]
    manifest_sha256: HexDigest
    suite_sha256: HexDigest
    case_count: Annotated[StrictInt, Field(gt=0)]


class InferenceParameters(StrictModel):
    """Portable inference controls; provider-specific configuration is hash-bound."""

    temperature: Annotated[StrictFloat, Field(ge=0.0, le=2.0, allow_inf_nan=False)] | None
    top_p: Annotated[StrictFloat, Field(gt=0.0, le=1.0, allow_inf_nan=False)] | None
    max_output_tokens: Annotated[StrictInt, Field(gt=0)]
    seed: StrictInt | None
    reasoning_effort: Literal["none", "minimal", "low", "medium", "high", "xhigh"] | None
    response_format: Literal[
        "strict_json_schema",
        "json_schema",
        "json_object",
        "text_then_validated",
    ]
    request_timeout_ms: Annotated[StrictInt, Field(gt=0)]


class OutputSchemaBinding(StrictModel):
    """Identify the inference schema; external schemas are explicitly distinguished."""

    kind: Literal["criteriabench_current", "external"]
    schema_id: NonEmptyText
    schema_sha256: HexDigest

    @model_validator(mode="after")
    def current_identifier_matches_kind(self) -> OutputSchemaBinding:
        is_current = self.schema_id == CURRENT_OUTPUT_SCHEMA_ID
        if (self.kind == "criteriabench_current") != is_current:
            raise ValueError("output schema kind and schema_id disagree")
        return self


class TokenPricing(StrictModel):
    """Hash-bound token rates used to independently recompute case-level USD costs."""

    currency: Literal["USD"]
    pricing_id: NonEmptyText
    pricing_sha256: HexDigest
    input_usd_per_million_tokens: UsdAmount
    output_usd_per_million_tokens: UsdAmount
    rounding: Literal["usd_9dp_half_up"]

    @model_validator(mode="after")
    def pricing_hash_matches_snapshot(self) -> TokenPricing:
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
    """Secret-free run metadata for comparison, not a reproducibility guarantee."""

    run_id: Identifier
    created_at_utc: UtcTimestamp
    provider: Identifier
    model: NonEmptyText
    deployment: NonEmptyText | None
    api_version: NonEmptyText | None
    prompt_sha256: HexDigest
    output_schema: OutputSchemaBinding
    code_sha256: HexDigest
    config_sha256: HexDigest
    inference: InferenceParameters
    pricing: TokenPricing
    paid_inference: StrictBool
    network_used: StrictBool


class TokenUsage(StrictModel):
    input_tokens: Annotated[StrictInt, Field(ge=0)]
    output_tokens: Annotated[StrictInt, Field(ge=0)]
    total_tokens: Annotated[StrictInt, Field(ge=0)]

    @model_validator(mode="after")
    def total_is_exact(self) -> TokenUsage:
        if self.total_tokens != self.input_tokens + self.output_tokens:
            raise ValueError("total_tokens must equal input_tokens plus output_tokens")
        return self


class UsagePricedCost(StrictModel):
    """Producer-recorded cost, independently checked against run-level token rates."""

    input_cost_usd: UsdAmount
    output_cost_usd: UsdAmount
    total_cost_usd: UsdAmount

    @model_validator(mode="after")
    def total_is_exact(self) -> UsagePricedCost:
        expected = Decimal(self.input_cost_usd) + Decimal(self.output_cost_usd)
        if Decimal(self.total_cost_usd) != expected:
            raise ValueError("total_cost_usd must equal input_cost_usd plus output_cost_usd")
        return self


class UsageRecord(StrictModel):
    availability: Literal["observed", "unavailable"]
    attempt_scope: Literal["all_attempts_including_retries"]
    tokens: TokenUsage
    cost: UsagePricedCost

    @model_validator(mode="after")
    def unavailable_usage_is_zero_placeholder(self) -> UsageRecord:
        if self.availability == "unavailable":
            if self.tokens.total_tokens != 0 or Decimal(self.cost.total_cost_usd) != 0:
                raise ValueError("unavailable usage requires zero token and cost placeholders")
        return self


class CaseExecution(StrictModel):
    case_path: Annotated[str, Field(pattern=r"^case_[0-9]{3}\.json$")]
    case_sha256: HexDigest
    trial_id: Annotated[str, Field(min_length=1, max_length=100)]
    request_sha256: HexDigest
    latency_ms: Annotated[StrictInt, Field(ge=0)]
    retries: Annotated[StrictInt, Field(ge=0)]
    usage: UsageRecord


class CompletedPrediction(CaseExecution):
    status: Literal["completed"]
    raw_response_sha256: HexDigest
    prediction_sha256: HexDigest
    prediction: ClinicalTrialEligibility


FailureKind = Literal[
    "authentication",
    "authorization",
    "content_filter",
    "invalid_json",
    "network",
    "provider_error",
    "rate_limit",
    "refusal",
    "schema_validation",
    "timeout",
    "trial_id_mismatch",
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
    """Hashable bundle body. The sealed artifact adds only ``bundle_sha256``."""

    schema_version: Literal["prediction-bundle-v1"]
    dataset: DatasetBinding
    run: RunProvenance
    cases: Annotated[list[CasePrediction], Field(min_length=1)]

    @model_validator(mode="after")
    def case_identities_and_costs_are_valid(self) -> PredictionBundlePayload:
        paths = [case.case_path for case in self.cases]
        trial_ids = [case.trial_id for case in self.cases]
        if len(set(paths)) != len(paths):
            raise ValueError("prediction bundle contains duplicate case paths")
        if len(set(trial_ids)) != len(trial_ids):
            raise ValueError("prediction bundle contains duplicate trial IDs")
        if self.dataset.case_count != len(self.cases):
            raise ValueError("dataset case_count must equal the number of case predictions")

        pricing = self.run.pricing
        for case in self.cases:
            usage = case.usage
            if usage.availability == "unavailable":
                continue
            expected_input = _price_tokens(
                usage.tokens.input_tokens,
                pricing.input_usd_per_million_tokens,
            )
            expected_output = _price_tokens(
                usage.tokens.output_tokens,
                pricing.output_usd_per_million_tokens,
            )
            if Decimal(usage.cost.input_cost_usd) != expected_input:
                raise ValueError("observed input cost does not match run-level token pricing")
            if Decimal(usage.cost.output_cost_usd) != expected_output:
                raise ValueError("observed output cost does not match run-level token pricing")

        if not self.run.paid_inference:
            if any(Decimal(case.usage.cost.total_cost_usd) != 0 for case in self.cases):
                raise ValueError("unpaid inference cannot declare a non-zero usage-priced cost")
        return self


class PredictionBundle(PredictionBundlePayload):
    """Self-identifying canonical prediction artifact."""

    bundle_sha256: HexDigest


class UsageSummary(StrictModel):
    """Observed totals; monetary values are lower bounds when usage is incomplete."""

    attempt_scope: Literal["all_attempts_including_retries"]
    observed_case_count: Annotated[StrictInt, Field(ge=0)]
    unavailable_case_count: Annotated[StrictInt, Field(ge=0)]
    completeness: Annotated[float, Field(ge=0.0, le=1.0)]
    monetary_totals_are_lower_bounds: StrictBool
    input_tokens: Annotated[StrictInt, Field(ge=0)]
    output_tokens: Annotated[StrictInt, Field(ge=0)]
    total_tokens: Annotated[StrictInt, Field(ge=0)]
    input_cost_usd: UsdAmount
    output_cost_usd: UsdAmount
    total_cost_usd: UsdAmount
    total_latency_ms: Annotated[StrictInt, Field(ge=0)]
    mean_latency_ms: Annotated[float, Field(ge=0.0, allow_inf_nan=False)]
    total_retries: Annotated[StrictInt, Field(ge=0)]

    @model_validator(mode="after")
    def totals_and_completeness_are_consistent(self) -> UsageSummary:
        case_count = self.observed_case_count + self.unavailable_case_count
        if case_count <= 0:
            raise ValueError("usage summary must cover at least one case")
        expected_completeness = round(self.observed_case_count / case_count, 6)
        if self.completeness != expected_completeness:
            raise ValueError("usage completeness does not match observed case coverage")
        expected_lower_bound = self.unavailable_case_count > 0
        if self.monetary_totals_are_lower_bounds != expected_lower_bound:
            raise ValueError("monetary lower-bound flag does not match usage completeness")
        if self.total_tokens != self.input_tokens + self.output_tokens:
            raise ValueError("usage summary total_tokens arithmetic mismatch")
        expected_cost = Decimal(self.input_cost_usd) + Decimal(self.output_cost_usd)
        if Decimal(self.total_cost_usd) != expected_cost:
            raise ValueError("usage summary total_cost_usd arithmetic mismatch")
        return self


class CaseScore(StrictModel):
    case_path: str
    trial_id: str
    status: Literal["completed", "failed"]
    failure_kind: FailureKind | None
    criterion_text_true_positives: Annotated[StrictInt, Field(ge=0)]
    criterion_text_f1: Annotated[float, Field(ge=0.0, le=1.0)]
    token_f1: Annotated[float, Field(ge=0.0, le=1.0)]
    macro_field_accuracy: Annotated[float, Field(ge=0.0, le=1.0)]

    @model_validator(mode="after")
    def failure_kind_matches_status(self) -> CaseScore:
        if (self.status == "failed") != (self.failure_kind is not None):
            raise ValueError("failure_kind must be present exactly when status is failed")
        return self


class PredictionScoreReport(StrictModel):
    schema_version: Literal["prediction-score-v1"]
    scoring_contract: Literal["failures-score-zero-v1"]
    bundle_sha256: HexDigest
    dataset: DatasetBinding
    run: RunProvenance
    completion_rate: Annotated[float, Field(ge=0.0, le=1.0)]
    schema_valid_rate: Annotated[float, Field(ge=0.0, le=1.0)]
    completed_cases: Annotated[StrictInt, Field(ge=0)]
    failed_cases: Annotated[StrictInt, Field(ge=0)]
    primary_all_cases: MetricAggregate
    completed_only_diagnostic: MetricAggregate | None
    failure_counts: dict[FailureKind, Annotated[StrictInt, Field(gt=0)]]
    taxonomy: ErrorTaxonomySummary
    usage: UsageSummary
    cases: Annotated[list[CaseScore], Field(min_length=1)]

    @model_validator(mode="after")
    def report_counts_and_rates_are_consistent(self) -> PredictionScoreReport:
        case_count = len(self.cases)
        completed = sum(case.status == "completed" for case in self.cases)
        failed = case_count - completed
        if self.completed_cases != completed or self.failed_cases != failed:
            raise ValueError("report completion counts do not match case statuses")
        if self.completed_cases + self.failed_cases != case_count:
            raise ValueError("report completed and failed counts do not cover all cases")
        if self.dataset.case_count != case_count:
            raise ValueError("report dataset case_count does not match cases")
        if self.primary_all_cases.case_count != case_count:
            raise ValueError("primary aggregate case_count does not match cases")
        usage_count = self.usage.observed_case_count + self.usage.unavailable_case_count
        if usage_count != case_count:
            raise ValueError("usage summary case counts do not match report cases")
        expected_rate = round(completed / case_count, 6)
        if self.completion_rate != expected_rate or self.schema_valid_rate != expected_rate:
            raise ValueError("report completion or schema-valid rate is inconsistent")
        if sum(self.failure_counts.values()) != failed:
            raise ValueError("failure_counts do not match failed cases")
        if completed == 0:
            if self.completed_only_diagnostic is not None:
                raise ValueError("completed-only diagnostic must be null with no completions")
        elif (
            self.completed_only_diagnostic is None
            or self.completed_only_diagnostic.case_count != completed
        ):
            raise ValueError("completed-only diagnostic case_count is inconsistent")
        return self


def _price_tokens(token_count: int, usd_per_million_tokens: str) -> Decimal:
    return (Decimal(token_count) * Decimal(usd_per_million_tokens) / _TOKENS_PER_MILLION).quantize(
        _USD_QUANTUM, rounding=ROUND_HALF_UP
    )


def _canonical_object_sha256(value: dict[str, str]) -> str:
    raw = json.dumps(
        value,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()
