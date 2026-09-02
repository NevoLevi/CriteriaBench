"""Build and verify the public, development-only LLF canary preregistration.

The builder is intentionally offline. It opens only the source-only generation
snapshot, physical development references/coverage, audited Python sources, and
``uv.lock``. It has no provider, network, environment, or secret entry point.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from collections import Counter
from collections.abc import Mapping, Sequence
from decimal import Decimal
from pathlib import Path
from typing import Annotated, Literal, Self, cast

from pydantic import BaseModel, Field, StrictBool, StrictFloat, StrictInt, model_validator

import criteriabench.real.llf_semantics as llf_semantics
import criteriabench.real_live.planning as live_planning
from criteriabench.domain.schemas import StrictModel
from criteriabench.real.llf_baselines import (
    BASELINE_ALGORITHM_CONTRACT_SHA256,
    FROZEN_BASELINE_IDENTITY,
    build_llf_bm25_baseline,
)
from criteriabench.real.llf_semantics import (
    PARSER_VERSION,
    LlfGenerationCase,
    LlfMatchCounts,
    LlfScoringReference,
    LlfSemanticComparison,
    canonical_llf_scoring_sha256,
    compare_llf_semantics,
    llf_semantic_components,
    load_llf_scoring_references,
)
from criteriabench.real_eval.integrity import canonical_sha256, case_set_sha256
from criteriabench.real_eval.llf_binding import load_llf_generation_split
from criteriabench.real_eval.llf_live_score import (
    EVALUATOR_ID,
    REPORT_SCHEMA_VERSION,
    LlfLiveScoreReport,
    evaluator_code_sha256,
)
from criteriabench.real_eval.models import (
    GenerationCase,
    GenerationDatasetBinding,
    MatchCountsModel,
)
from criteriabench.real_live.contracts import (
    CANARY_BUDGET_CAP_USD,
    CANARY_CASE_COUNT,
    LLF_ENGINEERING_LIMITS,
    LLF_ENGINEERING_LIMITS_SHA256,
    LLF_PROMPT_EXAMPLE_TRIAL_IDS,
    MAX_INPUT_TOKENS_RESERVED,
    MAX_OUTPUT_TOKENS,
    MAXIMUM_ATTEMPTS,
    RESERVATION_PER_CASE_USD,
    CanaryExecutionBinding,
    CanaryExecutionBindingPayload,
    FrozenExecutionImplementation,
    FrozenLunaConfiguration,
    FrozenOutputContract,
    FrozenPricing,
    LivePlan,
    PaidAuthorization,
    caller_execution_identity_sha256,
    freeze_output_contract,
    frozen_execution_implementation,
    frozen_luna_configuration,
    frozen_pricing,
    llf_semantic_output_contract,
    money,
)
from criteriabench.real_live.planning import (
    CANARY_SELECTION_ALGORITHM,
    CANARY_SELECTION_SEED,
    select_development_canary,
)

SCHEMA_VERSION = "llf-canary-preregistration-v1"
ARTIFACT_ID = "criteriabench-real-v1-llf-development-canary-25"
ARTIFACT_PURPOSE = (
    "Pre-model development gate for deciding whether a separately authorized locked-test run "
    "may proceed; this artifact is not locked-test evidence."
)
EXPECTED_DEVELOPMENT_CASES = 200
EXPECTED_DEVELOPMENT_TRIALS = 86
MAX_ARTIFACT_BYTES = 2_000_000

HexDigest = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
CaseId = Annotated[str, Field(pattern=r"^NCT[0-9]{8}_[0-9]+$")]
TrialId = Annotated[str, Field(pattern=r"^NCT[0-9]{8}$")]
RunIdentifier = Annotated[
    str,
    Field(min_length=1, max_length=200, pattern=r"^[A-Za-z0-9]+(?:[._-][A-Za-z0-9]+)*$"),
]


class DevelopmentReferenceBinding(StrictModel):
    reference_path: Literal["development_references.jsonl"]
    reference_artifact_sha256: HexDigest
    reference_artifact_bytes: Annotated[StrictInt, Field(gt=0)]
    coverage_path: Literal["llf-semantic-coverage-development.json"]
    coverage_sha256: HexDigest
    coverage_artifact_bytes: Annotated[StrictInt, Field(gt=0)]
    operational_case_count: Literal[200]
    semantic_case_count: Literal[200]
    missing_upstream_case_count: Literal[0]
    selected_reference_set_sha256: HexDigest


class CanaryCaseBinding(StrictModel):
    ordinal: Annotated[StrictInt, Field(gt=0, le=25)]
    case_id: CaseId
    trial_id: TrialId
    document_id: CaseId
    criterion_kind: Literal["inclusion", "exclusion"]
    source_sha256: HexDigest
    source_character_count: Annotated[StrictInt, Field(gt=0)]
    source_utf8_byte_count: Annotated[StrictInt, Field(gt=0)]
    source_length_bin: Literal["short", "medium", "long"]

    @model_validator(mode="after")
    def byte_count_can_hold_characters(self) -> Self:
        if self.source_utf8_byte_count < self.source_character_count:
            raise ValueError("UTF-8 byte count cannot be smaller than character count")
        return self


class CanarySelectionBinding(StrictModel):
    algorithm: Literal["polarity-length-tertile-trial-stratified-sha256-v1"]
    seed: Literal["criteriabench-real-v1-luna-canary"]
    prompt_example_trials_excluded: Literal[True]
    development_population_case_count: Literal[200]
    development_population_trial_count: Literal[86]
    eligible_population_case_count: Annotated[StrictInt, Field(gt=0, le=200)]
    eligible_population_trial_count: Annotated[StrictInt, Field(gt=0, le=86)]
    lower_source_character_cut: Annotated[StrictInt, Field(gt=0)]
    upper_source_character_cut: Annotated[StrictInt, Field(gt=0)]
    selected_case_count: Literal[25]
    selected_trial_count: Literal[25]
    selected_case_set_sha256: HexDigest
    selection_rows_sha256: HexDigest
    cases: Annotated[tuple[CanaryCaseBinding, ...], Field(min_length=25, max_length=25)]

    @model_validator(mode="after")
    def selected_rows_are_exact(self) -> Self:
        if self.lower_source_character_cut > self.upper_source_character_cut:
            raise ValueError("source-length cuts are reversed")
        if [case.ordinal for case in self.cases] != list(range(1, 26)):
            raise ValueError("canary ordinals must be contiguous")
        if len({case.case_id for case in self.cases}) != 25:
            raise ValueError("canary case IDs must be unique")
        if len({case.trial_id for case in self.cases}) != 25:
            raise ValueError("canary must contain one case per trial")
        if self.selection_rows_sha256 != canonical_sha256(
            [case.model_dump(mode="json") for case in self.cases]
        ):
            raise ValueError("selection row seal does not reproduce")
        expected_case_set = canonical_sha256(
            [
                {
                    "case_id": case.case_id,
                    "trial_id": case.trial_id,
                    "document_id": case.document_id,
                    "criterion_kind": case.criterion_kind,
                    "source_sha256": case.source_sha256,
                }
                for case in self.cases
            ]
        )
        if self.selected_case_set_sha256 != expected_case_set:
            raise ValueError("selected source-only case-set seal does not reproduce")
        return self


class BaselineIdentityBinding(StrictModel):
    schema_version: Literal["llf-retrieval-baseline-identity-v1"]
    baseline_id: Literal["llf-bm25-nearest-development-v1"]
    configuration_sha256: HexDigest
    code_sha256: HexDigest
    algorithm_contract_sha256: HexDigest
    identity_sha256: HexDigest
    training_case_count: Literal[200]
    training_trial_count: Literal[86]
    training_set_sha256: HexDigest
    prediction_set_sha256: HexDigest
    development_prediction_policy: Literal["leave-entire-target-trial-out"]

    @model_validator(mode="after")
    def identity_is_the_current_frozen_baseline(self) -> Self:
        expected = FROZEN_BASELINE_IDENTITY.as_dict()
        actual = self.model_dump(
            mode="json",
            include={
                "schema_version",
                "baseline_id",
                "configuration_sha256",
                "code_sha256",
                "identity_sha256",
            },
        )
        if actual != expected:
            raise ValueError("baseline identity differs from the frozen implementation")
        if self.algorithm_contract_sha256 != BASELINE_ALGORITHM_CONTRACT_SHA256:
            raise ValueError("baseline algorithm-contract hash differs from the frozen contract")
        return self


class BaselineMetricSuite(StrictModel):
    semantic_case_count: Literal[25]
    exact_match_count: Annotated[StrictInt, Field(ge=0, le=25)]
    exact_match_accuracy: Annotated[StrictFloat, Field(ge=0.0, le=1.0)]
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
    def aggregates_reproduce(self) -> Self:
        if self.exact_match_accuracy != _six(self.exact_match_count / 25):
            raise ValueError("baseline exact accuracy does not match its count")
        if _counts(self.primary_structure) != _sum_counts(self.nodes, self.edges):
            raise ValueError("baseline primary structure must equal nodes plus edges")
        if _counts(self.typed_components) != _sum_counts(
            self.calls,
            self.method_attributes,
            self.symbols,
            self.strings,
            self.booleans,
        ):
            raise ValueError("baseline typed components must equal their components")
        return self


class CanaryComposition(StrictModel):
    criterion_kind_counts: dict[Literal["inclusion", "exclusion"], StrictInt]
    source_length_bin_counts: dict[Literal["short", "medium", "long"], StrictInt]
    selection_stratum_counts: dict[str, StrictInt]
    reference_node_count_bins: dict[Literal["1-5", "6-10", "11+"], StrictInt]
    reference_edge_count_bins: dict[Literal["0-4", "5-9", "10+"], StrictInt]
    complexity_disclosure: Literal[
        "aggregate development-reference counts only; no per-case reference complexity disclosed"
    ]

    @model_validator(mode="after")
    def every_slice_covers_the_canary(self) -> Self:
        groups = (
            self.criterion_kind_counts,
            self.source_length_bin_counts,
            self.selection_stratum_counts,
            self.reference_node_count_bins,
            self.reference_edge_count_bins,
        )
        if any(any(value < 0 for value in counts.values()) for counts in groups):
            raise ValueError("composition counts cannot be negative")
        if any(sum(counts.values()) != 25 for counts in groups):
            raise ValueError("every composition slice must cover exactly 25 cases")
        return self


class EngineeringLimitsBinding(StrictModel):
    policy_id: Literal["llf-live-engineering-limits-v1"]
    logical_form_characters: Literal[8192]
    logical_form_utf8_bytes: Literal[16384]
    semantic_nodes: Literal[256]
    semantic_depth: Literal[64]
    call_arguments: Literal[32]
    collection_items: Literal[32]
    identifier_characters: Literal[128]
    string_utf8_bytes: Literal[1024]
    policy_sha256: HexDigest
    selection_policy: Literal["predeclared_split_independent_engineering_and_security_limits"]
    development_reference_statistics_used: Literal[False]
    locked_test_reference_statistics_used: Literal[False]

    @model_validator(mode="after")
    def exact_runtime_policy_is_current(self) -> Self:
        policy = self.model_dump(
            mode="json",
            include={
                "policy_id",
                "logical_form_characters",
                "logical_form_utf8_bytes",
                "semantic_nodes",
                "semantic_depth",
                "call_arguments",
                "collection_items",
                "identifier_characters",
                "string_utf8_bytes",
            },
        )
        if policy != LLF_ENGINEERING_LIMITS:
            raise ValueError("engineering limits differ from the frozen runtime policy")
        if self.policy_sha256 != LLF_ENGINEERING_LIMITS_SHA256:
            raise ValueError("engineering-limits semantic hash differs from runtime")
        return self


class ImplementationBinding(StrictModel):
    selection_module_sha256: HexDigest
    llf_semantics_module_sha256: HexDigest
    parser_version: Literal["bounded-python-ast-allowlist-v1"]
    live_evaluator_id: Literal["criteriabench.real_eval.llf_live_score:v1"]
    live_evaluator_code_sha256: HexDigest
    preregistration_module_sha256: HexDigest
    engineering_limits: EngineeringLimitsBinding
    execution: FrozenExecutionImplementation

    @model_validator(mode="after")
    def direct_code_hashes_are_current(self) -> Self:
        if self.selection_module_sha256 != _module_sha256(live_planning):
            raise ValueError("selection module hash is stale")
        if self.llf_semantics_module_sha256 != _module_sha256(llf_semantics):
            raise ValueError("LLF semantics module hash is stale")
        if self.live_evaluator_code_sha256 != evaluator_code_sha256():
            raise ValueError("live evaluator code hash is stale")
        if (
            self.preregistration_module_sha256
            != hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
        ):
            raise ValueError("preregistration module hash is stale")
        return self


class PlannedPaidCall(StrictModel):
    output_contract: FrozenOutputContract
    luna: FrozenLunaConfiguration
    pricing: FrozenPricing
    caller_execution_identity_sha256: HexDigest
    case_count: Literal[25]
    reservation_input_tokens_per_case: Literal[16384]
    reservation_output_tokens_per_case: Literal[2048]
    reservation_per_case_usd: Literal["0.006553600"]
    reserved_total_usd: Literal["0.163840000"]
    hard_budget_cap_usd: Literal["0.170000000"]
    maximum_attempts_per_case: Literal[1]
    exact_plan_and_fresh_authorization_required_before_paid_execution: Literal[True]
    refresh_pricing_if_execution_is_outside_frozen_validity: Literal[True]

    def verify_caller_identity(self, execution: FrozenExecutionImplementation) -> None:
        expected = caller_execution_identity_sha256(self.luna, execution)
        if self.caller_execution_identity_sha256 != expected:
            raise ValueError("caller execution identity does not match Luna and implementation")


class AdvancementGates(StrictModel):
    decision_rule: Literal["all_gates_must_pass"]
    report_schema_version: Literal["llf-live-score-report-v1"]
    required_purpose: Literal["development_llf_canary_25"]
    required_split: Literal["development"]
    required_terminal_state: Literal["completed"]
    required_attempted_count: Literal[25]
    required_not_attempted_count: Literal[0]
    required_completed_count: Literal[25]
    required_failed_count: Literal[0]
    required_fatal_abort: Literal[False]
    required_usage_known_count: Literal[25]
    required_usage_unknown_count: Literal[0]
    required_latency_observed_count: Literal[25]
    required_complete_timing_count: Literal[25]
    required_external_attempt_claim_count: Literal[25]
    required_unique_external_attempt_claim_count: Literal[25]
    required_response_id_count: Literal[25]
    required_unique_response_id_count: Literal[25]
    required_provider_model: Literal["gpt-5.6-luna"]
    required_provider_model_count: Literal[25]
    required_provider_response_object: Literal["response"]
    required_provider_response_object_count: Literal[25]
    required_provider_service_tier: Literal["default"]
    required_provider_service_tier_count: Literal[25]
    required_provider_hash_count: Literal[25]
    maximum_charged_total_usd: Literal["0.170000000"]
    minimum_primary_structure_f1: Annotated[StrictFloat, Field(ge=0.5, le=1.0)]
    minimum_primary_structure_uplift_over_bm25: Annotated[StrictFloat, Field(ge=0.1, le=1.0)]
    resulting_minimum_primary_structure_f1: Annotated[StrictFloat, Field(ge=0.5, le=1.0)]
    minimum_exact_match_count: Annotated[StrictInt, Field(ge=2, le=25)]
    maximum_p95_latency_ms: Annotated[StrictFloat, Field(gt=0.0, le=60_000.0)]
    maximum_attempts_per_case: Literal[1]
    sdk_retries: Literal[0]
    app_retries: Literal[0]
    on_any_failure: Literal["do_not_authorize_or_run_locked_test"]


class EvidenceScope(StrictModel):
    uses_only_development_generation_split: Literal[True]
    uses_only_development_references: Literal[True]
    locked_test_references_opened: Literal[False]
    model_or_provider_called: Literal[False]
    network_used: Literal[False]
    environment_or_secret_read: Literal[False]
    locked_test_evidence: Literal[False]
    claim: Literal[
        "development-only preregistration and baseline; not an estimate of locked-test performance"
    ]


class CanaryPreregistrationPayload(StrictModel):
    schema_version: Literal["llf-canary-preregistration-v1"]
    artifact_id: Literal["criteriabench-real-v1-llf-development-canary-25"]
    artifact_purpose: str
    evidence_scope: EvidenceScope
    generation_dataset: GenerationDatasetBinding
    development_reference: DevelopmentReferenceBinding
    selection: CanarySelectionBinding
    baseline: BaselineIdentityBinding
    baseline_metrics: BaselineMetricSuite
    composition: CanaryComposition
    implementation: ImplementationBinding
    planned_paid_call: PlannedPaidCall
    advancement_gates: AdvancementGates
    limitations: tuple[str, ...]

    @model_validator(mode="after")
    def cross_bindings_are_consistent(self) -> Self:
        if self.artifact_purpose != ARTIFACT_PURPOSE:
            raise ValueError("artifact purpose is not the frozen development-only claim")
        if (
            self.generation_dataset.split != "development"
            or self.generation_dataset.case_count != EXPECTED_DEVELOPMENT_CASES
        ):
            raise ValueError("preregistration must bind the full development generation split")
        if self.selection.selected_case_set_sha256 == self.generation_dataset.case_set_sha256:
            raise ValueError("canary case-set seal cannot equal the full development seal")
        expected_gate = _six(
            max(
                self.advancement_gates.minimum_primary_structure_f1,
                self.baseline_metrics.primary_structure.f1
                + self.advancement_gates.minimum_primary_structure_uplift_over_bm25,
            )
        )
        if self.advancement_gates.resulting_minimum_primary_structure_f1 != expected_gate:
            raise ValueError("resulting quality threshold does not reproduce from both gates")
        if self.planned_paid_call.output_contract.track != "llf_semantic_ast":
            raise ValueError("canary preregistration must use the lossless LLF track")
        self.planned_paid_call.verify_caller_identity(self.implementation.execution)
        if (
            self.planned_paid_call.hard_budget_cap_usd
            != self.advancement_gates.maximum_charged_total_usd
        ):
            raise ValueError("planned cap and advancement cap differ")
        if (
            self.planned_paid_call.maximum_attempts_per_case
            != self.advancement_gates.maximum_attempts_per_case
        ):
            raise ValueError("planned attempt limit and advancement gate differ")
        expected_kind_counts = dict(
            sorted(Counter(case.criterion_kind for case in self.selection.cases).items())
        )
        expected_length_counts = dict(
            sorted(Counter(case.source_length_bin for case in self.selection.cases).items())
        )
        expected_strata = dict(
            sorted(
                Counter(
                    f"{case.criterion_kind}:{case.source_length_bin}"
                    for case in self.selection.cases
                ).items()
            )
        )
        if (
            self.composition.criterion_kind_counts != expected_kind_counts
            or self.composition.source_length_bin_counts != expected_length_counts
            or self.composition.selection_stratum_counts != expected_strata
        ):
            raise ValueError("source-only composition counts differ from selected rows")
        return self


class CanaryPreregistration(CanaryPreregistrationPayload):
    preregistration_sha256: HexDigest

    @model_validator(mode="after")
    def seal_matches_payload(self) -> Self:
        payload = self.model_dump(mode="json", exclude={"preregistration_sha256"})
        if self.preregistration_sha256 != canonical_sha256(payload):
            raise ValueError("preregistration seal does not match its canonical payload")
        return self


class GateCheck(StrictModel):
    gate_id: str
    passed: StrictBool
    observed: str
    requirement: str


class CanaryAdvancementDecisionPayload(StrictModel):
    schema_version: Literal["llf-canary-advancement-decision-v1"]
    decision_rule: Literal["all_gates_must_pass"]
    preregistration_sha256: HexDigest
    preregistration_artifact_sha256: HexDigest
    execution_binding_sha256: HexDigest
    execution_binding_artifact_sha256: HexDigest
    plan_sha256: HexDigest
    plan_artifact_sha256: HexDigest
    runtime_image_id: Annotated[str, Field(pattern=r"^sha256:[0-9a-f]{64}$")]
    run_id: RunIdentifier
    authorization_id: RunIdentifier
    authorization_sha256: HexDigest
    authorization_artifact_sha256: HexDigest
    authorization_claim_sha256: HexDigest
    authorization_claim_artifact_sha256: HexDigest
    authorization_consumption_sha256: HexDigest
    authorization_consumption_artifact_sha256: HexDigest
    score_report_sha256: HexDigest
    score_report_artifact_sha256: HexDigest
    external_attempt_claim_count: Literal[25]
    external_attempt_claim_inventory_sha256: HexDigest
    external_attempt_claim_artifact_inventory_sha256: HexDigest
    advancement_status: Literal["pass", "fail"]
    proceed_to_separate_locked_authorization: StrictBool
    checks: tuple[GateCheck, ...]

    @model_validator(mode="after")
    def decision_is_conjunctive(self) -> Self:
        if not self.checks:
            raise ValueError("advancement decision requires checks")
        if len({check.gate_id for check in self.checks}) != len(self.checks):
            raise ValueError("advancement gate IDs must be unique")
        passed = all(check.passed for check in self.checks)
        if self.proceed_to_separate_locked_authorization != passed:
            raise ValueError("advancement decision must be the conjunction of every gate")
        if self.advancement_status != ("pass" if passed else "fail"):
            raise ValueError("advancement status must match the conjunctive gate result")
        return self


class CanaryAdvancementDecision(CanaryAdvancementDecisionPayload):
    decision_sha256: HexDigest

    @model_validator(mode="after")
    def seal_matches_payload(self) -> Self:
        payload = self.model_dump(mode="json", exclude={"decision_sha256"})
        if self.decision_sha256 != canonical_sha256(payload):
            raise ValueError("advancement decision seal does not match its payload")
        return self


def build_llf_canary_preregistration(
    *, dataset_dir: Path, coverage_dir: Path
) -> CanaryPreregistration:
    """Build the deterministic, provider-free 25-case development preregistration."""

    dataset_root = _regular_directory(dataset_dir, "dataset")
    coverage_root = _regular_directory(coverage_dir, "coverage")
    reference_path = _direct_regular_file(dataset_root, "development_references.jsonl")
    coverage_path = _direct_regular_file(coverage_root, "llf-semantic-coverage-development.json")
    generation = load_llf_generation_split(dataset_root, "development")
    if len({case.trial_id for case in generation.cases}) != EXPECTED_DEVELOPMENT_TRIALS:
        raise ValueError("development trial denominator changed")
    reference_corpus = load_llf_scoring_references(
        reference_path, coverage_path, split="development"
    )
    if reference_corpus.missing_upstream_case_ids:
        raise ValueError("development canary cannot be preregistered with missing references")

    selected = select_development_canary(generation.cases)
    semantic_cases = tuple(_semantic_case(case) for case in generation.cases)
    selected_semantic = tuple(_semantic_case(case) for case in selected)
    references = reference_corpus.references
    reference_by_id = {reference.case_id: reference for reference in references}
    selected_references = tuple(_selected_reference(case, reference_by_id) for case in selected)
    baseline = build_llf_bm25_baseline(semantic_cases, references)
    predictions = baseline.predict_many(selected_semantic)
    comparisons = tuple(
        compare_llf_semantics(prediction, reference.reference)
        for prediction, reference in zip(predictions, selected_references, strict=True)
    )
    baseline_metrics = _aggregate_metrics(comparisons)

    lower_cut, upper_cut, eligible_cases, eligible_trials = _source_length_cuts(generation.cases)
    case_rows = tuple(
        CanaryCaseBinding(
            ordinal=ordinal,
            case_id=case.case_id,
            trial_id=case.trial_id,
            document_id=case.document_id,
            criterion_kind=cast(Literal["inclusion", "exclusion"], case.criterion_kind.value),
            source_sha256=case.source_sha256,
            source_character_count=len(case.source_text),
            source_utf8_byte_count=len(case.source_text.encode("utf-8")),
            source_length_bin=_source_length_bin(len(case.source_text), lower_cut, upper_cut),
        )
        for ordinal, case in enumerate(selected, start=1)
    )
    selection = CanarySelectionBinding(
        algorithm=CANARY_SELECTION_ALGORITHM,
        seed=CANARY_SELECTION_SEED,
        prompt_example_trials_excluded=True,
        development_population_case_count=len(generation.cases),
        development_population_trial_count=len({case.trial_id for case in generation.cases}),
        eligible_population_case_count=eligible_cases,
        eligible_population_trial_count=eligible_trials,
        lower_source_character_cut=lower_cut,
        upper_source_character_cut=upper_cut,
        selected_case_count=len(selected),
        selected_trial_count=len({case.trial_id for case in selected}),
        selected_case_set_sha256=case_set_sha256(selected),
        selection_rows_sha256=canonical_sha256(
            [case.model_dump(mode="json") for case in case_rows]
        ),
        cases=case_rows,
    )
    prediction_set_sha256 = canonical_sha256(
        [
            {
                "ordinal": ordinal,
                "case_id": case.case_id,
                "prediction_sha256": canonical_llf_scoring_sha256(prediction),
            }
            for ordinal, (case, prediction) in enumerate(
                zip(selected, predictions, strict=True), start=1
            )
        ]
    )
    selected_reference_set_sha256 = canonical_sha256(
        [
            {
                "case_id": reference.case_id,
                "trial_id": reference.trial_id,
                "source_sha256": reference.source_sha256,
                "reference_sha256": reference.reference_sha256,
            }
            for reference in selected_references
        ]
    )
    identity = FROZEN_BASELINE_IDENTITY
    baseline_binding = BaselineIdentityBinding(
        **identity.as_dict(),
        algorithm_contract_sha256=BASELINE_ALGORITHM_CONTRACT_SHA256,
        training_case_count=baseline.training_case_count,
        training_trial_count=baseline.training_trial_count,
        training_set_sha256=baseline.training_set_sha256,
        prediction_set_sha256=prediction_set_sha256,
        development_prediction_policy="leave-entire-target-trial-out",
    )

    contract = llf_semantic_output_contract()
    execution = frozen_execution_implementation()
    luna = frozen_luna_configuration()
    planned_call = PlannedPaidCall(
        output_contract=freeze_output_contract(contract),
        luna=luna,
        pricing=frozen_pricing(),
        caller_execution_identity_sha256=caller_execution_identity_sha256(luna, execution),
        case_count=CANARY_CASE_COUNT,
        reservation_input_tokens_per_case=MAX_INPUT_TOKENS_RESERVED,
        reservation_output_tokens_per_case=MAX_OUTPUT_TOKENS,
        reservation_per_case_usd=money(RESERVATION_PER_CASE_USD),
        reserved_total_usd=money(RESERVATION_PER_CASE_USD * CANARY_CASE_COUNT),
        hard_budget_cap_usd=money(CANARY_BUDGET_CAP_USD),
        maximum_attempts_per_case=MAXIMUM_ATTEMPTS,
        exact_plan_and_fresh_authorization_required_before_paid_execution=True,
        refresh_pricing_if_execution_is_outside_frozen_validity=True,
    )
    gates = AdvancementGates(
        decision_rule="all_gates_must_pass",
        report_schema_version=REPORT_SCHEMA_VERSION,
        required_purpose="development_llf_canary_25",
        required_split="development",
        required_terminal_state="completed",
        required_attempted_count=25,
        required_not_attempted_count=0,
        required_completed_count=25,
        required_failed_count=0,
        required_fatal_abort=False,
        required_usage_known_count=25,
        required_usage_unknown_count=0,
        required_latency_observed_count=25,
        required_complete_timing_count=25,
        required_external_attempt_claim_count=25,
        required_unique_external_attempt_claim_count=25,
        required_response_id_count=25,
        required_unique_response_id_count=25,
        required_provider_model="gpt-5.6-luna",
        required_provider_model_count=25,
        required_provider_response_object="response",
        required_provider_response_object_count=25,
        required_provider_service_tier="default",
        required_provider_service_tier_count=25,
        required_provider_hash_count=25,
        maximum_charged_total_usd=money(CANARY_BUDGET_CAP_USD),
        minimum_primary_structure_f1=0.5,
        minimum_primary_structure_uplift_over_bm25=0.1,
        resulting_minimum_primary_structure_f1=_six(
            max(0.5, baseline_metrics.primary_structure.f1 + 0.1)
        ),
        minimum_exact_match_count=2,
        maximum_p95_latency_ms=60_000.0,
        maximum_attempts_per_case=MAXIMUM_ATTEMPTS,
        sdk_retries=planned_call.luna.sdk_max_retries,
        app_retries=planned_call.luna.app_max_retries,
        on_any_failure="do_not_authorize_or_run_locked_test",
    )
    implementation = ImplementationBinding(
        selection_module_sha256=_module_sha256(live_planning),
        llf_semantics_module_sha256=_module_sha256(llf_semantics),
        parser_version=PARSER_VERSION,
        live_evaluator_id=EVALUATOR_ID,
        live_evaluator_code_sha256=evaluator_code_sha256(),
        preregistration_module_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        engineering_limits=EngineeringLimitsBinding(
            **LLF_ENGINEERING_LIMITS,
            policy_sha256=LLF_ENGINEERING_LIMITS_SHA256,
            selection_policy=("predeclared_split_independent_engineering_and_security_limits"),
            development_reference_statistics_used=False,
            locked_test_reference_statistics_used=False,
        ),
        execution=execution,
    )
    payload = CanaryPreregistrationPayload(
        schema_version=SCHEMA_VERSION,
        artifact_id=ARTIFACT_ID,
        artifact_purpose=ARTIFACT_PURPOSE,
        evidence_scope=EvidenceScope(
            uses_only_development_generation_split=True,
            uses_only_development_references=True,
            locked_test_references_opened=False,
            model_or_provider_called=False,
            network_used=False,
            environment_or_secret_read=False,
            locked_test_evidence=False,
            claim=(
                "development-only preregistration and baseline; not an estimate of "
                "locked-test performance"
            ),
        ),
        generation_dataset=generation.dataset,
        development_reference=DevelopmentReferenceBinding(
            reference_path="development_references.jsonl",
            reference_artifact_sha256=reference_corpus.reference_artifact_sha256,
            reference_artifact_bytes=reference_path.stat().st_size,
            coverage_path="llf-semantic-coverage-development.json",
            coverage_sha256=reference_corpus.coverage_sha256,
            coverage_artifact_bytes=coverage_path.stat().st_size,
            operational_case_count=reference_corpus.operational_case_count,
            semantic_case_count=reference_corpus.semantic_case_count,
            missing_upstream_case_count=len(reference_corpus.missing_upstream_case_ids),
            selected_reference_set_sha256=selected_reference_set_sha256,
        ),
        selection=selection,
        baseline=baseline_binding,
        baseline_metrics=baseline_metrics,
        composition=_composition(case_rows, selected_references),
        implementation=implementation,
        planned_paid_call=planned_call,
        advancement_gates=gates,
        limitations=(
            (
                "The canary and BM25 comparator use development annotations and are not "
                "held-out results."
            ),
            "The 25-case canary is a go/no-go smoke test, not a precise performance estimate.",
            (
                "Passing permits only a new, exact, separately authorized locked-test plan; "
                "it does not authorize one."
            ),
            "The pricing snapshot must be refreshed if it is stale before any paid request.",
            (
                "No clinical-validity or deployment-safety claim follows from this structural "
                "LLF benchmark."
            ),
        ),
    )
    body = payload.model_dump(mode="json")
    return CanaryPreregistration.model_validate(
        {**body, "preregistration_sha256": canonical_sha256(body)}
    )


def verify_canary_plan_matches_preregistration(
    preregistration: CanaryPreregistration,
    plan: LivePlan,
) -> None:
    """Reject any paid-plan field that differs from the public preregistration."""

    planned = preregistration.planned_paid_call
    expected_case_rows = tuple(
        (
            row.ordinal,
            row.case_id,
            row.trial_id,
            row.document_id,
            row.criterion_kind,
            row.source_sha256,
        )
        for row in preregistration.selection.cases
    )
    actual_case_rows = tuple(
        (
            row.ordinal,
            row.case_id,
            row.trial_id,
            row.document_id,
            row.criterion_kind,
            row.source_sha256,
        )
        for row in plan.cases
    )
    comparisons: tuple[tuple[str, object, object], ...] = (
        ("purpose", plan.purpose, "development_llf_canary_25"),
        ("source_dataset", plan.source_dataset, preregistration.generation_dataset),
        (
            "selected_case_set_sha256",
            plan.selected_case_set_sha256,
            preregistration.selection.selected_case_set_sha256,
        ),
        ("selection_algorithm", plan.selection_algorithm, preregistration.selection.algorithm),
        ("cases", actual_case_rows, expected_case_rows),
        ("output_contract", plan.output_contract, planned.output_contract),
        ("luna", plan.luna, planned.luna),
        (
            "execution_implementation",
            plan.execution_implementation,
            preregistration.implementation.execution,
        ),
        ("pricing", plan.pricing, planned.pricing),
        (
            "reservation_input_tokens",
            plan.reservation_input_tokens,
            planned.reservation_input_tokens_per_case,
        ),
        (
            "reservation_output_tokens",
            plan.reservation_output_tokens,
            planned.reservation_output_tokens_per_case,
        ),
        (
            "reservation_per_case_usd",
            plan.reservation_per_case_usd,
            planned.reservation_per_case_usd,
        ),
        ("reserved_total_usd", plan.reserved_total_usd, planned.reserved_total_usd),
        ("budget_cap_usd", plan.budget_cap_usd, planned.hard_budget_cap_usd),
        ("case_count", len(plan.cases), planned.case_count),
        (
            "requires_separate_locked_authorization",
            plan.requires_separate_locked_authorization,
            False,
        ),
    )
    for label, observed, expected in comparisons:
        if observed != expected:
            raise ValueError(f"canary plan {label} differs from the public preregistration")
    planned.verify_caller_identity(plan.execution_implementation)


def build_canary_execution_binding(
    *,
    preregistration: CanaryPreregistration,
    plan: LivePlan,
    plan_artifact_sha256: str,
    intended_run_id: str,
    intended_authorization_id: str,
    host_output_directory_sha256: str,
    authorization_state_directory_sha256: str,
) -> CanaryExecutionBinding:
    """Seal one public execution identity after an exact paid plan exists."""

    verify_canary_plan_matches_preregistration(preregistration, plan)
    payload = CanaryExecutionBindingPayload(
        schema_version="llf-canary-execution-binding-v1",
        preregistration_sha256=preregistration.preregistration_sha256,
        preregistration_artifact_sha256=hashlib.sha256(
            preregistration_bytes(preregistration)
        ).hexdigest(),
        plan_sha256=plan.plan_sha256,
        plan_artifact_sha256=plan_artifact_sha256,
        runtime_image_id=plan.runtime_image_id,
        runtime_output_directory="/run/artifacts/output",
        runtime_output_directory_sha256=live_planning.run_directory_sha256("/run/artifacts/output"),
        host_output_directory_sha256=host_output_directory_sha256,
        authorization_state_directory_sha256=authorization_state_directory_sha256,
        authorization_claim_filename_template="claim-{authorization_sha256}.json",
        intended_run_id=intended_run_id,
        intended_authorization_id=intended_authorization_id,
        purpose="development_llf_canary_25",
        case_count=len(plan.cases),
        selected_case_set_sha256=plan.selected_case_set_sha256,
        source_dataset=plan.source_dataset,
        selection_algorithm=plan.selection_algorithm,
        output_contract=plan.output_contract,
        luna=plan.luna,
        execution=plan.execution_implementation,
        pricing=plan.pricing,
        reservation_input_tokens=plan.reservation_input_tokens,
        reservation_output_tokens=plan.reservation_output_tokens,
        reservation_per_case_usd=plan.reservation_per_case_usd,
        reserved_total_usd=plan.reserved_total_usd,
        budget_cap_usd=plan.budget_cap_usd,
        advancement_gates_sha256=canonical_sha256(
            preregistration.advancement_gates.model_dump(mode="json")
        ),
        requires_separate_locked_authorization=plan.requires_separate_locked_authorization,
        maximum_execution_count=1,
        optional_stopping_prohibited=True,
        quality_failure_policy=("new_versioned_configuration_and_new_preregistration_required"),
        operational_rerun_policy=(
            "new_public_execution_binding_fresh_authorization_and_all_attempts_disclosed"
        ),
    )
    body = payload.model_dump(mode="json")
    return CanaryExecutionBinding.model_validate(
        {**body, "execution_binding_sha256": canonical_sha256(body)}
    )


def verify_canary_execution_binding(
    preregistration: CanaryPreregistration,
    binding: CanaryExecutionBinding,
    plan: LivePlan,
    *,
    plan_artifact_sha256: str,
) -> None:
    """Reproduce a public execution binding against the exact current artifacts."""

    verify_canary_plan_matches_preregistration(preregistration, plan)
    if binding.preregistration_sha256 != preregistration.preregistration_sha256:
        raise ValueError("execution binding names a different preregistration")
    if (
        binding.preregistration_artifact_sha256
        != hashlib.sha256(preregistration_bytes(preregistration)).hexdigest()
    ):
        raise ValueError("execution binding names different preregistration bytes")
    expected = build_canary_execution_binding(
        preregistration=preregistration,
        plan=plan,
        plan_artifact_sha256=plan_artifact_sha256,
        intended_run_id=binding.intended_run_id,
        intended_authorization_id=binding.intended_authorization_id,
        host_output_directory_sha256=binding.host_output_directory_sha256,
        authorization_state_directory_sha256=(binding.authorization_state_directory_sha256),
    )
    if binding != expected:
        raise ValueError("execution binding does not reproduce from the exact plan")


def _verify_binding_matches_preregistration(
    preregistration: CanaryPreregistration,
    binding: CanaryExecutionBinding,
) -> None:
    planned = preregistration.planned_paid_call
    comparisons: tuple[tuple[str, object, object], ...] = (
        (
            "preregistration_sha256",
            binding.preregistration_sha256,
            preregistration.preregistration_sha256,
        ),
        (
            "preregistration_artifact_sha256",
            binding.preregistration_artifact_sha256,
            hashlib.sha256(preregistration_bytes(preregistration)).hexdigest(),
        ),
        ("purpose", binding.purpose, "development_llf_canary_25"),
        ("case_count", binding.case_count, planned.case_count),
        (
            "selected_case_set_sha256",
            binding.selected_case_set_sha256,
            preregistration.selection.selected_case_set_sha256,
        ),
        ("source_dataset", binding.source_dataset, preregistration.generation_dataset),
        ("selection_algorithm", binding.selection_algorithm, preregistration.selection.algorithm),
        ("output_contract", binding.output_contract, planned.output_contract),
        ("luna", binding.luna, planned.luna),
        ("execution", binding.execution, preregistration.implementation.execution),
        ("pricing", binding.pricing, planned.pricing),
        (
            "reservation_input_tokens",
            binding.reservation_input_tokens,
            planned.reservation_input_tokens_per_case,
        ),
        (
            "reservation_output_tokens",
            binding.reservation_output_tokens,
            planned.reservation_output_tokens_per_case,
        ),
        (
            "reservation_per_case_usd",
            binding.reservation_per_case_usd,
            planned.reservation_per_case_usd,
        ),
        ("reserved_total_usd", binding.reserved_total_usd, planned.reserved_total_usd),
        ("budget_cap_usd", binding.budget_cap_usd, planned.hard_budget_cap_usd),
        (
            "advancement_gates_sha256",
            binding.advancement_gates_sha256,
            canonical_sha256(preregistration.advancement_gates.model_dump(mode="json")),
        ),
        (
            "requires_separate_locked_authorization",
            binding.requires_separate_locked_authorization,
            False,
        ),
    )
    for label, observed, expected in comparisons:
        if observed != expected:
            raise ValueError(f"execution binding {label} differs from the preregistration")
    planned.verify_caller_identity(binding.execution)


def evaluate_canary_advancement(
    preregistration: CanaryPreregistration,
    execution_binding: CanaryExecutionBinding,
    plan: LivePlan,
    authorization: PaidAuthorization,
    report: LlfLiveScoreReport,
) -> CanaryAdvancementDecision:
    """Seal a conjunctive decision for one exact public canary execution chain."""

    preregistration_artifact_sha256 = hashlib.sha256(
        preregistration_bytes(preregistration)
    ).hexdigest()
    execution_binding_artifact_sha256 = hashlib.sha256(
        execution_binding_bytes(execution_binding)
    ).hexdigest()
    plan_artifact_sha256 = hashlib.sha256(_compact_model_bytes(plan)).hexdigest()
    authorization_artifact_sha256 = hashlib.sha256(_compact_model_bytes(authorization)).hexdigest()
    score_report_artifact_sha256 = hashlib.sha256(_compact_model_bytes(report)).hexdigest()
    verify_canary_execution_binding(
        preregistration,
        execution_binding,
        plan,
        plan_artifact_sha256=plan_artifact_sha256,
    )
    gates = preregistration.advancement_gates
    operational = report.operational
    provider = operational.provider
    usage = operational.usage
    latency = operational.latency
    expected_model_hash = hashlib.sha256(gates.required_provider_model.encode()).hexdigest()
    expected_object_hash = hashlib.sha256(
        gates.required_provider_response_object.encode()
    ).hexdigest()
    expected_service_tier_hash = hashlib.sha256(
        gates.required_provider_service_tier.encode()
    ).hexdigest()
    checks: list[GateCheck] = []

    def check(gate_id: str, passed: bool, observed: object, requirement: str) -> None:
        checks.append(
            GateCheck(
                gate_id=gate_id,
                passed=passed,
                observed=_display(observed),
                requirement=requirement,
            )
        )

    actual_case_rows = tuple(
        (case.ordinal, case.case_id, case.trial_id, case.source_sha256) for case in report.cases
    )
    bound_case_rows = tuple(
        (case.ordinal, case.case_id, case.trial_id, case.source_sha256)
        for case in preregistration.selection.cases
    )
    exact_execution_identity = (
        report.inputs.preregistration_sha256 == preregistration.preregistration_sha256
        and report.inputs.preregistration_artifact_sha256 == preregistration_artifact_sha256
        and report.inputs.execution_binding_sha256 == execution_binding.execution_binding_sha256
        and report.inputs.execution_binding_artifact_sha256 == execution_binding_artifact_sha256
        and report.inputs.plan_sha256 == execution_binding.plan_sha256
        and report.inputs.plan_sha256 == plan.plan_sha256
        and report.inputs.plan_artifact_sha256 == execution_binding.plan_artifact_sha256
        and report.inputs.plan_artifact_sha256 == plan_artifact_sha256
        and report.inputs.runtime_image_id == execution_binding.runtime_image_id
        and report.inputs.runtime_image_id == plan.runtime_image_id
        and report.inputs.run_id == execution_binding.intended_run_id
        and report.inputs.run_directory_sha256 == execution_binding.runtime_output_directory_sha256
        and report.inputs.host_run_directory_sha256
        == execution_binding.host_output_directory_sha256
        and report.inputs.authorization_state_directory_sha256
        == execution_binding.authorization_state_directory_sha256
        and report.inputs.authorization_sha256 == authorization.authorization_sha256
        and report.inputs.authorization_artifact_sha256 == authorization_artifact_sha256
        and authorization.authorization_id == execution_binding.intended_authorization_id
        and authorization.plan_sha256 == execution_binding.plan_sha256
        and authorization.purpose == execution_binding.purpose
        and authorization.run_id == execution_binding.intended_run_id
        and authorization.authorized_case_count == execution_binding.case_count
        and authorization.authorized_budget_cap_usd == execution_binding.budget_cap_usd
        and authorization.preregistration_sha256 == preregistration.preregistration_sha256
        and authorization.preregistration_artifact_sha256 == preregistration_artifact_sha256
        and authorization.execution_binding_sha256 == execution_binding.execution_binding_sha256
        and authorization.execution_binding_artifact_sha256 == execution_binding_artifact_sha256
        and authorization.run_directory_sha256 == execution_binding.runtime_output_directory_sha256
        and authorization.host_run_directory_sha256
        == execution_binding.host_output_directory_sha256
        and authorization.authorization_state_directory_sha256
        == execution_binding.authorization_state_directory_sha256
    )
    check(
        "sealed_inputs",
        exact_execution_identity
        and report.schema_version == gates.report_schema_version
        and report.purpose == gates.required_purpose
        and report.split == gates.required_split
        and report.evaluator_id == preregistration.implementation.live_evaluator_id
        and report.evaluator_code_sha256
        == preregistration.implementation.live_evaluator_code_sha256
        and report.inputs.generation_dataset == preregistration.generation_dataset
        and report.inputs.reference_artifact_sha256
        == preregistration.development_reference.reference_artifact_sha256
        and report.inputs.split_coverage_sha256
        == preregistration.development_reference.coverage_sha256
        and report.inputs.output_contract == preregistration.planned_paid_call.output_contract
        and report.inputs.execution_implementation_sha256
        == preregistration.implementation.execution.implementation_sha256
        and report.inputs.execution_package_python_inventory_sha256
        == preregistration.implementation.execution.package_python_inventory_sha256
        and actual_case_rows == bound_case_rows,
        {
            "report_sha256": report.report_sha256,
            "execution_binding_sha256": execution_binding.execution_binding_sha256,
            "plan_sha256": report.inputs.plan_sha256,
            "run_id": report.inputs.run_id,
            "authorization_id": authorization.authorization_id,
            "authorization_sha256": report.inputs.authorization_sha256,
            "authorization_claim_sha256": report.inputs.authorization_claim_sha256,
            "score_report_artifact_sha256": score_report_artifact_sha256,
        },
        (
            "exact public preregistration/execution binding, plan, image, run, authorization, "
            "inputs, cases, contract, implementation, and scorer"
        ),
    )
    external_claim_hashes = {case.external_attempt_claim_sha256 for case in report.cases}
    external_claim_artifact_hashes = {
        case.external_attempt_claim_artifact_sha256 for case in report.cases
    }
    check(
        "external_attempt_ledger_complete",
        report.inputs.external_attempt_claim_count == gates.required_external_attempt_claim_count
        and len(external_claim_hashes) == gates.required_unique_external_attempt_claim_count
        and len(external_claim_artifact_hashes)
        == gates.required_unique_external_attempt_claim_count
        and all(case.failure_kind != "interrupted_unknown" for case in report.cases),
        {
            "count": report.inputs.external_attempt_claim_count,
            "unique_claims": len(external_claim_hashes),
            "unique_claim_artifacts": len(external_claim_artifact_hashes),
            "interrupted": sum(case.failure_kind == "interrupted_unknown" for case in report.cases),
        },
        "one unique durable external paid-attempt claim per ordinal and no interruption",
    )
    check(
        "complete_no_fatal_run",
        operational.plan_case_count == gates.required_attempted_count
        and operational.completed_count == gates.required_completed_count
        and operational.failed_count == gates.required_failed_count,
        {
            "plan": operational.plan_case_count,
            "completed": operational.completed_count,
            "failed": operational.failed_count,
        },
        "sealed scorer accepted a completed 25/25 run with zero failures or fatal abort",
    )
    check(
        "usage_complete",
        usage.usage_known_count == gates.required_usage_known_count
        and usage.usage_unknown_count == gates.required_usage_unknown_count,
        {"known": usage.usage_known_count, "unknown": usage.usage_unknown_count},
        "known priced usage for all 25 cases and zero unknown usage",
    )
    check(
        "responses_provenance_complete",
        provider.response_id_count == gates.required_response_id_count
        and provider.response_id_missing_count == 0
        and provider.unique_response_id_count == gates.required_unique_response_id_count
        and provider.provider_model_counts
        == {gates.required_provider_model: gates.required_provider_model_count}
        and provider.provider_model_sha256_counts
        == {expected_model_hash: gates.required_provider_hash_count}
        and provider.provider_response_object_counts
        == {
            gates.required_provider_response_object: (gates.required_provider_response_object_count)
        }
        and provider.provider_response_object_sha256_counts
        == {expected_object_hash: gates.required_provider_hash_count}
        and provider.provider_service_tier_counts
        == {gates.required_provider_service_tier: (gates.required_provider_service_tier_count)}
        and provider.provider_service_tier_sha256_counts
        == {expected_service_tier_hash: gates.required_provider_hash_count},
        provider.model_dump(mode="json"),
        "25 unique response IDs plus exact Luna Responses/default-tier provenance",
    )
    check(
        "hard_budget",
        Decimal(usage.charged_total_usd) <= Decimal(gates.maximum_charged_total_usd),
        usage.charged_total_usd,
        f"charged total <= ${gates.maximum_charged_total_usd}",
    )
    check(
        "absolute_primary_quality",
        report.metrics.primary_structure.f1 >= gates.minimum_primary_structure_f1,
        report.metrics.primary_structure.f1,
        f"combined node+edge micro F1 >= {gates.minimum_primary_structure_f1:.6f}",
    )
    observed_uplift = _six(
        report.metrics.primary_structure.f1 - preregistration.baseline_metrics.primary_structure.f1
    )
    check(
        "bm25_uplift",
        observed_uplift >= gates.minimum_primary_structure_uplift_over_bm25,
        observed_uplift,
        (
            "combined node+edge micro F1 uplift over exact canary BM25 >= "
            f"{gates.minimum_primary_structure_uplift_over_bm25:.6f}"
        ),
    )
    check(
        "exact_matches",
        report.metrics.exact_match_count >= gates.minimum_exact_match_count,
        report.metrics.exact_match_count,
        f"exact matches >= {gates.minimum_exact_match_count}/25",
    )
    check(
        "latency",
        latency.observed_case_count == gates.required_latency_observed_count
        and latency.complete_timing_count == gates.required_complete_timing_count
        and latency.unobserved_case_count == 0
        and latency.p95_latency_ms is not None
        and latency.p95_latency_ms <= gates.maximum_p95_latency_ms,
        {
            "observed": latency.observed_case_count,
            "complete_timing": latency.complete_timing_count,
            "unobserved": latency.unobserved_case_count,
            "p95_ms": latency.p95_latency_ms,
        },
        f"25 observed latencies and p95 <= {gates.maximum_p95_latency_ms:.0f} ms",
    )
    check(
        "no_retries",
        preregistration.planned_paid_call.maximum_attempts_per_case == 1
        and preregistration.planned_paid_call.luna.sdk_max_retries == 0
        and preregistration.planned_paid_call.luna.app_max_retries == 0
        and len({case.attempt_sha256 for case in report.cases}) == 25
        and len({case.attempt_artifact_sha256 for case in report.cases}) == 25,
        {
            "attempts": len({case.attempt_sha256 for case in report.cases}),
            "attempt_artifacts": len({case.attempt_artifact_sha256 for case in report.cases}),
            "sdk_retries": preregistration.planned_paid_call.luna.sdk_max_retries,
            "app_retries": preregistration.planned_paid_call.luna.app_max_retries,
        },
        "one sealed attempt per case; SDK and application retries both zero",
    )
    decision_checks = tuple(checks)
    passed = all(item.passed for item in decision_checks)
    payload = CanaryAdvancementDecisionPayload(
        schema_version="llf-canary-advancement-decision-v1",
        decision_rule="all_gates_must_pass",
        preregistration_sha256=preregistration.preregistration_sha256,
        preregistration_artifact_sha256=preregistration_artifact_sha256,
        execution_binding_sha256=execution_binding.execution_binding_sha256,
        execution_binding_artifact_sha256=execution_binding_artifact_sha256,
        plan_sha256=plan.plan_sha256,
        plan_artifact_sha256=plan_artifact_sha256,
        runtime_image_id=plan.runtime_image_id,
        run_id=execution_binding.intended_run_id,
        authorization_id=authorization.authorization_id,
        authorization_sha256=authorization.authorization_sha256,
        authorization_artifact_sha256=authorization_artifact_sha256,
        authorization_claim_sha256=report.inputs.authorization_claim_sha256,
        authorization_claim_artifact_sha256=(report.inputs.authorization_claim_artifact_sha256),
        authorization_consumption_sha256=(report.inputs.authorization_consumption_sha256),
        authorization_consumption_artifact_sha256=(
            report.inputs.authorization_consumption_artifact_sha256
        ),
        score_report_sha256=report.report_sha256,
        score_report_artifact_sha256=score_report_artifact_sha256,
        external_attempt_claim_count=report.inputs.external_attempt_claim_count,
        external_attempt_claim_inventory_sha256=(
            report.inputs.external_attempt_claim_inventory_sha256
        ),
        external_attempt_claim_artifact_inventory_sha256=(
            report.inputs.external_attempt_claim_artifact_inventory_sha256
        ),
        advancement_status="pass" if passed else "fail",
        proceed_to_separate_locked_authorization=passed,
        checks=decision_checks,
    )
    body = payload.model_dump(mode="json")
    return CanaryAdvancementDecision.model_validate(
        {**body, "decision_sha256": canonical_sha256(body)}
    )


def preregistration_bytes(preregistration: CanaryPreregistration) -> bytes:
    """Return byte-stable, newline-terminated public JSON."""

    return (
        json.dumps(
            preregistration.model_dump(mode="json"),
            ensure_ascii=False,
            allow_nan=False,
            indent=2,
            sort_keys=True,
        )
        + "\n"
    ).encode("utf-8")


def execution_binding_bytes(binding: CanaryExecutionBinding) -> bytes:
    """Return byte-stable, newline-terminated public execution-binding JSON."""

    return (
        json.dumps(
            binding.model_dump(mode="json"),
            ensure_ascii=False,
            allow_nan=False,
            indent=2,
            sort_keys=True,
        )
        + "\n"
    ).encode("utf-8")


def advancement_decision_bytes(decision: CanaryAdvancementDecision) -> bytes:
    """Return byte-stable, newline-terminated public advancement-decision JSON."""

    return (
        json.dumps(
            decision.model_dump(mode="json"),
            ensure_ascii=False,
            allow_nan=False,
            indent=2,
            sort_keys=True,
        )
        + "\n"
    ).encode("utf-8")


def _read_artifact_bytes(path: Path, label: str) -> bytes:
    if path.is_symlink():
        raise ValueError(f"{label} artifact cannot be a symbolic link")
    resolved = path.resolve(strict=True)
    if not resolved.is_file():
        raise ValueError(f"{label} artifact is not a regular file")
    return resolved.read_bytes()


def load_preregistration(path: Path) -> CanaryPreregistration:
    raw = _read_artifact_bytes(path, "preregistration")
    if len(raw) > MAX_ARTIFACT_BYTES:
        raise ValueError("preregistration artifact exceeds its offline size limit")
    artifact = CanaryPreregistration.model_validate_json(raw)
    if preregistration_bytes(artifact) != raw:
        raise ValueError("preregistration artifact is not canonical public JSON")
    return artifact


def load_execution_binding(path: Path) -> CanaryExecutionBinding:
    raw = _read_artifact_bytes(path, "execution-binding")
    if len(raw) > MAX_ARTIFACT_BYTES:
        raise ValueError("execution-binding artifact exceeds its offline size limit")
    artifact = CanaryExecutionBinding.model_validate_json(raw)
    if execution_binding_bytes(artifact) != raw:
        raise ValueError("execution-binding artifact is not canonical public JSON")
    return artifact


def load_advancement_decision(path: Path) -> CanaryAdvancementDecision:
    raw = _read_artifact_bytes(path, "advancement-decision")
    if len(raw) > MAX_ARTIFACT_BYTES:
        raise ValueError("advancement-decision artifact exceeds its offline size limit")
    artifact = CanaryAdvancementDecision.model_validate_json(raw)
    if advancement_decision_bytes(artifact) != raw:
        raise ValueError("advancement-decision artifact is not canonical public JSON")
    return artifact


def load_live_plan(path: Path) -> tuple[LivePlan, str]:
    """Load one exact canonical live-plan artifact and return its file digest."""

    raw = _read_artifact_bytes(path, "live-plan")
    if len(raw) > MAX_ARTIFACT_BYTES:
        raise ValueError("live-plan artifact exceeds its offline size limit")
    plan = LivePlan.model_validate_json(raw)
    if _compact_model_bytes(plan) != raw:
        raise ValueError("live-plan artifact is not canonical runner JSON")
    return plan, hashlib.sha256(raw).hexdigest()


def load_paid_authorization(path: Path) -> tuple[PaidAuthorization, str]:
    """Load one exact canonical paid-authorization artifact and its file digest."""

    raw = _read_artifact_bytes(path, "paid-authorization")
    if len(raw) > MAX_ARTIFACT_BYTES:
        raise ValueError("paid-authorization artifact exceeds its offline size limit")
    authorization = PaidAuthorization.model_validate_json(raw)
    if _compact_model_bytes(authorization) != raw:
        raise ValueError("paid-authorization artifact is not canonical runner JSON")
    return authorization, hashlib.sha256(raw).hexdigest()


def load_live_score_report(path: Path) -> tuple[LlfLiveScoreReport, str]:
    """Load one exact canonical live score report and its file digest."""

    raw = _read_artifact_bytes(path, "live-score")
    if len(raw) > MAX_ARTIFACT_BYTES:
        raise ValueError("live-score artifact exceeds its offline size limit")
    report = LlfLiveScoreReport.model_validate_json(raw)
    if _compact_model_bytes(report) != raw:
        raise ValueError("live-score artifact is not canonical scorer JSON")
    return report, hashlib.sha256(raw).hexdigest()


def verify_canary_advancement_decision(
    preregistration: CanaryPreregistration,
    execution_binding: CanaryExecutionBinding,
    plan: LivePlan,
    authorization: PaidAuthorization,
    report: LlfLiveScoreReport,
    decision: CanaryAdvancementDecision,
) -> None:
    """Recompute one canonical decision and reject any chain or decision difference."""

    expected = evaluate_canary_advancement(
        preregistration,
        execution_binding,
        plan,
        authorization,
        report,
    )
    if decision != expected:
        raise ValueError("advancement decision does not reproduce from the exact canary chain")


def _semantic_case(case: GenerationCase) -> LlfGenerationCase:
    polarity = case.criterion_kind.value
    if polarity not in {"inclusion", "exclusion"}:
        raise ValueError("LLF generation case has unknown polarity")
    return LlfGenerationCase(
        case_id=case.case_id,
        trial_id=case.trial_id,
        split="development",
        polarity=cast(Literal["inclusion", "exclusion"], polarity),
        source_text=case.source_text,
        source_sha256=case.source_sha256,
    )


def _selected_reference(
    case: GenerationCase,
    reference_by_id: Mapping[str, LlfScoringReference],
) -> LlfScoringReference:
    reference = reference_by_id.get(case.case_id)
    if reference is None:
        raise ValueError(f"selected canary case has no development reference: {case.case_id}")
    if reference.trial_id != case.trial_id or reference.source_sha256 != case.source_sha256:
        raise ValueError("selected reference identity differs from source-only generation case")
    return reference


def _source_length_cuts(cases: Sequence[GenerationCase]) -> tuple[int, int, int, int]:
    eligible = tuple(case for case in cases if case.trial_id not in LLF_PROMPT_EXAMPLE_TRIAL_IDS)
    lengths = sorted(len(case.source_text) for case in eligible)
    if not lengths:
        raise ValueError("no development cases remain after prompt-example exclusion")
    return (
        lengths[len(lengths) // 3],
        lengths[(2 * len(lengths)) // 3],
        len(eligible),
        len({case.trial_id for case in eligible}),
    )


def _source_length_bin(
    character_count: int, lower_cut: int, upper_cut: int
) -> Literal["short", "medium", "long"]:
    if character_count <= lower_cut:
        return "short"
    if character_count <= upper_cut:
        return "medium"
    return "long"


def _aggregate_metrics(comparisons: Sequence[LlfSemanticComparison]) -> BaselineMetricSuite:
    if len(comparisons) != CANARY_CASE_COUNT:
        raise ValueError("baseline metric aggregate requires exactly 25 comparisons")

    def aggregate(name: str) -> MatchCountsModel:
        total = LlfMatchCounts(0, 0, 0)
        for comparison in comparisons:
            total += cast(LlfMatchCounts, getattr(comparison, name))
        return _metric(total)

    exact = sum(comparison.exact_match for comparison in comparisons)
    return BaselineMetricSuite(
        semantic_case_count=len(comparisons),
        exact_match_count=exact,
        exact_match_accuracy=_six(exact / len(comparisons)),
        primary_structure=aggregate("structure"),
        nodes=aggregate("nodes"),
        edges=aggregate("edges"),
        calls=aggregate("calls"),
        method_attributes=aggregate("method_attributes"),
        symbols=aggregate("symbols"),
        strings=aggregate("strings"),
        booleans=aggregate("booleans"),
        typed_components=aggregate("typed_components"),
    )


def _metric(value: LlfMatchCounts) -> MatchCountsModel:
    return MatchCountsModel(
        true_positive=value.true_positive,
        false_positive=value.false_positive,
        false_negative=value.false_negative,
        precision=_six(value.precision),
        recall=_six(value.recall),
        f1=_six(value.f1),
    )


def _composition(
    cases: Sequence[CanaryCaseBinding], references: Sequence[LlfScoringReference]
) -> CanaryComposition:
    criterion_counts: Counter[str] = Counter(case.criterion_kind for case in cases)
    length_counts: Counter[str] = Counter(case.source_length_bin for case in cases)
    strata: Counter[str] = Counter(
        f"{case.criterion_kind}:{case.source_length_bin}" for case in cases
    )
    node_bins: Counter[str] = Counter()
    edge_bins: Counter[str] = Counter()
    for reference in references:
        node_count = len(reference.reference.nodes)
        edge_count = sum(llf_semantic_components(reference.reference).edges.values())
        node_bins[_bounded_bin(node_count, first=5, second=10, labels=("1-5", "6-10", "11+"))] += 1
        edge_bins[_bounded_bin(edge_count, first=4, second=9, labels=("0-4", "5-9", "10+"))] += 1
    return CanaryComposition(
        criterion_kind_counts=_complete_counts(criterion_counts, ("inclusion", "exclusion")),
        source_length_bin_counts=_complete_counts(length_counts, ("short", "medium", "long")),
        selection_stratum_counts=dict(sorted(strata.items())),
        reference_node_count_bins=_complete_counts(node_bins, ("1-5", "6-10", "11+")),
        reference_edge_count_bins=_complete_counts(edge_bins, ("0-4", "5-9", "10+")),
        complexity_disclosure=(
            "aggregate development-reference counts only; no per-case reference complexity "
            "disclosed"
        ),
    )


def _bounded_bin(
    value: int,
    *,
    first: int,
    second: int,
    labels: tuple[str, str, str],
) -> str:
    if value <= first:
        return labels[0]
    if value <= second:
        return labels[1]
    return labels[2]


def _complete_counts(counter: Counter[str], labels: Sequence[str]) -> dict[str, int]:
    return {label: counter[label] for label in labels}


def _counts(value: MatchCountsModel) -> tuple[int, int, int]:
    return value.true_positive, value.false_positive, value.false_negative


def _sum_counts(*values: MatchCountsModel) -> tuple[int, int, int]:
    rows = [_counts(value) for value in values]
    return cast(tuple[int, int, int], tuple(sum(items) for items in zip(*rows, strict=True)))


def _six(value: float) -> float:
    return round(value, 6)


def _module_sha256(module: object) -> str:
    path_value = getattr(module, "__file__", None)
    if not isinstance(path_value, str):
        raise ValueError("audited module has no source path")
    return hashlib.sha256(Path(path_value).read_bytes()).hexdigest()


def _regular_directory(path: Path, label: str) -> Path:
    if path.is_symlink():
        raise ValueError(f"{label} directory cannot be a symbolic link")
    root = path.resolve(strict=True)
    if not root.is_dir():
        raise ValueError(f"{label} path is not a directory")
    return root


def _direct_regular_file(root: Path, name: str) -> Path:
    if Path(name).name != name:
        raise ValueError("artifact name must be a direct child")
    candidate = root / name
    if candidate.is_symlink():
        raise ValueError(f"artifact cannot be a symbolic link: {name}")
    path = candidate.resolve(strict=True)
    if path.parent != root or not path.is_file():
        raise ValueError(f"artifact is not a direct regular file: {name}")
    return path


def _display(value: object) -> str:
    if isinstance(value, str):
        return value
    return json.dumps(
        value, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":")
    )


def _compact_model_bytes(model: BaseModel) -> bytes:
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


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    for name in ("build", "check"):
        command = subparsers.add_parser(name)
        command.add_argument("--dataset-dir", type=Path, required=True)
        command.add_argument("--coverage-dir", type=Path, required=True)
        if name == "build":
            command.add_argument("--output", type=Path, required=True)
        else:
            command.add_argument("--artifact", type=Path, required=True)
    bind = subparsers.add_parser("bind-execution")
    bind.add_argument("--preregistration", type=Path, required=True)
    bind.add_argument("--plan", type=Path, required=True)
    bind.add_argument("--intended-run-id", required=True)
    bind.add_argument("--intended-authorization-id", required=True)
    bind.add_argument("--host-output-directory-sha256", required=True)
    bind.add_argument("--authorization-state-directory-sha256", required=True)
    bind.add_argument("--output", type=Path, required=True)
    check_execution = subparsers.add_parser("check-execution")
    check_execution.add_argument("--preregistration", type=Path, required=True)
    check_execution.add_argument("--plan", type=Path, required=True)
    check_execution.add_argument("--artifact", type=Path, required=True)
    for name in ("decide", "check-decision"):
        command = subparsers.add_parser(name)
        command.add_argument("--preregistration", type=Path, required=True)
        command.add_argument("--execution-binding", type=Path, required=True)
        command.add_argument("--plan", type=Path, required=True)
        command.add_argument("--authorization", type=Path, required=True)
        command.add_argument("--score-report", type=Path, required=True)
        command.add_argument(
            "--output" if name == "decide" else "--artifact",
            type=Path,
            required=True,
        )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    try:
        if args.command in {"build", "check"}:
            preregistration = build_llf_canary_preregistration(
                dataset_dir=cast(Path, args.dataset_dir),
                coverage_dir=cast(Path, args.coverage_dir),
            )
            payload = preregistration_bytes(preregistration)
            if args.command == "build":
                _write_new_artifact(cast(Path, args.output), payload)
                print(
                    f"sealed LLF canary preregistration: {preregistration.preregistration_sha256}"
                )
            else:
                existing = load_preregistration(cast(Path, args.artifact))
                if existing != preregistration or preregistration_bytes(existing) != payload:
                    raise ValueError("preregistration does not exactly reproduce offline")
                print(
                    f"verified LLF canary preregistration: {preregistration.preregistration_sha256}"
                )
        elif args.command in {"bind-execution", "check-execution"}:
            preregistration = load_preregistration(cast(Path, args.preregistration))
            plan, plan_artifact_sha256 = load_live_plan(cast(Path, args.plan))
            if args.command == "bind-execution":
                binding = build_canary_execution_binding(
                    preregistration=preregistration,
                    plan=plan,
                    plan_artifact_sha256=plan_artifact_sha256,
                    intended_run_id=cast(str, args.intended_run_id),
                    intended_authorization_id=cast(str, args.intended_authorization_id),
                    host_output_directory_sha256=cast(str, args.host_output_directory_sha256),
                    authorization_state_directory_sha256=cast(
                        str, args.authorization_state_directory_sha256
                    ),
                )
                _write_new_artifact(cast(Path, args.output), execution_binding_bytes(binding))
                print(f"sealed LLF canary execution binding: {binding.execution_binding_sha256}")
            else:
                binding = load_execution_binding(cast(Path, args.artifact))
                verify_canary_execution_binding(
                    preregistration,
                    binding,
                    plan,
                    plan_artifact_sha256=plan_artifact_sha256,
                )
                print(f"verified LLF canary execution binding: {binding.execution_binding_sha256}")
        else:
            preregistration = load_preregistration(cast(Path, args.preregistration))
            binding = load_execution_binding(cast(Path, args.execution_binding))
            plan, _ = load_live_plan(cast(Path, args.plan))
            authorization, _ = load_paid_authorization(cast(Path, args.authorization))
            report, _ = load_live_score_report(cast(Path, args.score_report))
            if args.command == "decide":
                decision = evaluate_canary_advancement(
                    preregistration,
                    binding,
                    plan,
                    authorization,
                    report,
                )
                _write_new_artifact(cast(Path, args.output), advancement_decision_bytes(decision))
                print(
                    f"sealed LLF canary advancement decision: {decision.decision_sha256} "
                    f"({decision.advancement_status})"
                )
            else:
                decision = load_advancement_decision(cast(Path, args.artifact))
                verify_canary_advancement_decision(
                    preregistration,
                    binding,
                    plan,
                    authorization,
                    report,
                    decision,
                )
                print(
                    f"verified LLF canary advancement decision: {decision.decision_sha256} "
                    f"({decision.advancement_status})"
                )
    except (OSError, ValueError) as error:
        parser.error(str(error))
    return 0


def _write_new_artifact(path: Path, payload: bytes) -> None:
    path.parent.resolve(strict=True)
    with path.open("xb") as stream:
        stream.write(payload)
        stream.flush()
        os.fsync(stream.fileno())


if __name__ == "__main__":
    raise SystemExit(main())
