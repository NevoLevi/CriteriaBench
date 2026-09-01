"""Strict data contracts for the offline evaluation suite."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import Field, StrictBool, StrictInt, field_validator, model_validator

from criteriabench.domain.schemas import ClinicalTrialEligibility, StrictModel, TrialDocument
from criteriabench.evaluation.metrics import EvaluationReport

DATASET_VERSION = "synthetic-v0.1"
EXPECTED_CASE_COUNT = 80
SUITE_VERSION = "offline-suite-v0.1.1"
BaselineName = Literal["empty-v1", "rules-v1"]


def split_slices(value: str) -> tuple[str, ...]:
    """Parse the fixture provenance's canonical comma-separated slice encoding."""

    parts = tuple(value.split(","))
    if not parts or any(not _is_identifier(part) for part in parts):
        raise ValueError("slices must be non-empty lowercase identifiers")
    if len(set(parts)) != len(parts):
        raise ValueError("slices must be unique lowercase identifiers")
    return parts


def _is_identifier(value: str) -> bool:
    return bool(value) and value == value.casefold() and value.replace("_", "a").isalnum()


class AnnotationMetadata(StrictModel):
    authoring_status: Literal["single_author"]
    method: Literal["deterministic_templates"]
    review_status: Literal["independent_review_pending"]


class ManifestRecord(StrictModel):
    path: Annotated[str, Field(pattern=r"^case_[0-9]{3}\.json$")]
    sha256: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    family: Annotated[str, Field(pattern=r"^[a-z0-9]+(?:_[a-z0-9]+)*$")]
    slices: Annotated[list[str], Field(min_length=1)]
    has_reference: Literal[True]

    @field_validator("slices")
    @classmethod
    def slices_are_typed(cls, value: list[str]) -> list[str]:
        if any(not _is_identifier(item) for item in value):
            raise ValueError("slices must be non-empty lowercase identifiers")
        if len(set(value)) != len(value):
            raise ValueError("slices must be unique lowercase identifiers")
        return value

    @property
    def slice_names(self) -> tuple[str, ...]:
        return tuple(self.slices)


class DatasetManifest(StrictModel):
    dataset_version: Literal["synthetic-v0.1"]
    case_count: Literal[80]
    family_count: Annotated[StrictInt, Field(gt=0)]
    variants_per_family: Annotated[StrictInt, Field(gt=0)]
    family_counts: dict[str, Annotated[StrictInt, Field(gt=0)]]
    slice_counts: dict[str, Annotated[StrictInt, Field(gt=0)]]
    annotation: AnnotationMetadata
    clinical_validation: Literal[False]
    license: Literal["MIT"]
    records: Annotated[list[ManifestRecord], Field(min_length=80, max_length=80)]

    @model_validator(mode="after")
    def counts_match_records(self) -> DatasetManifest:
        expected_paths = [f"case_{index:03d}.json" for index in range(1, 81)]
        if [record.path for record in self.records] != expected_paths:
            raise ValueError("manifest records must be the ordered v0.1 case set")
        if len({record.sha256 for record in self.records}) != len(self.records):
            raise ValueError("manifest fixture hashes must be unique")

        family_counts: dict[str, int] = {}
        slice_counts: dict[str, int] = {}
        for record in self.records:
            family_counts[record.family] = family_counts.get(record.family, 0) + 1
            for slice_name in record.slice_names:
                slice_counts[slice_name] = slice_counts.get(slice_name, 0) + 1
        if self.family_counts != dict(sorted(family_counts.items())):
            raise ValueError("manifest family counts do not match records")
        if self.slice_counts != dict(sorted(slice_counts.items())):
            raise ValueError("manifest slice counts do not match records")
        if self.family_count != len(family_counts):
            raise ValueError("manifest family_count does not match records")
        if any(count != self.variants_per_family for count in family_counts.values()):
            raise ValueError("manifest family counts do not match variants_per_family")
        return self


class FixtureProvenance(StrictModel):
    kind: Literal["synthetic"]
    annotation_method: Literal["deterministic_template_v0.1"]
    family: Annotated[str, Field(pattern=r"^[a-z0-9]+(?:_[a-z0-9]+)*$")]
    slices: str
    review_status: Literal["independent_review_pending"]

    @field_validator("slices")
    @classmethod
    def slices_are_typed(cls, value: str) -> str:
        split_slices(value)
        return value


class OfflineBenchmarkFixture(StrictModel):
    """Local fixture boundary, intentionally independent of the general benchmark CLI."""

    fixture_version: Literal["synthetic-v0.1"]
    trial: TrialDocument
    reference: ClinicalTrialEligibility
    provenance: FixtureProvenance

    @model_validator(mode="after")
    def reference_matches_trial(self) -> OfflineBenchmarkFixture:
        if self.reference.trial_id != self.trial.trial_id:
            raise ValueError("reference trial_id must match the fixture trial_id")
        return self


class LoadedCase(StrictModel):
    path: str
    sha256: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    family: str
    slices: list[str]
    fixture: OfflineBenchmarkFixture


class LoadedSuite(StrictModel):
    manifest_path: str
    manifest_sha256: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    manifest: DatasetManifest
    cases: Annotated[list[LoadedCase], Field(min_length=80, max_length=80)]


class CaseLineage(StrictModel):
    """Derived family/template/variant identity without changing frozen fixture bytes."""

    trial_id: str
    family_id: Annotated[str, Field(pattern=r"^[a-z0-9]+(?:_[a-z0-9]+)*$")]
    base_template_id: Annotated[
        str,
        Field(pattern=r"^[a-z0-9]+(?:[-_][a-z0-9]+)*$"),
    ]
    variant_id: Annotated[
        str,
        Field(pattern=r"^[a-z0-9]+(?:[-_][a-z0-9]+)*$"),
    ]


class ErrorTaxonomy(StrictModel):
    missing_criterion: Annotated[StrictInt, Field(ge=0)] = 0
    spurious_criterion: Annotated[StrictInt, Field(ge=0)] = 0
    text_mismatch: Annotated[StrictInt, Field(ge=0)] = 0
    category_mismatch: Annotated[StrictInt, Field(ge=0)] = 0
    concept_mismatch: Annotated[StrictInt, Field(ge=0)] = 0
    operator_mismatch: Annotated[StrictInt, Field(ge=0)] = 0
    value_mismatch: Annotated[StrictInt, Field(ge=0)] = 0
    unit_mismatch: Annotated[StrictInt, Field(ge=0)] = 0
    negation_mismatch: Annotated[StrictInt, Field(ge=0)] = 0
    temporal_relation_mismatch: Annotated[StrictInt, Field(ge=0)] = 0
    temporal_quantity_mismatch: Annotated[StrictInt, Field(ge=0)] = 0
    temporal_unit_mismatch: Annotated[StrictInt, Field(ge=0)] = 0
    temporal_reference_event_mismatch: Annotated[StrictInt, Field(ge=0)] = 0
    temporal_raw_text_mismatch: Annotated[StrictInt, Field(ge=0)] = 0
    logic_connector_mismatch: Annotated[StrictInt, Field(ge=0)] = 0
    logic_parent_mismatch: Annotated[StrictInt, Field(ge=0)] = 0
    evidence_quote_mismatch: Annotated[StrictInt, Field(ge=0)] = 0
    evidence_offset_mismatch: Annotated[StrictInt, Field(ge=0)] = 0

    @property
    def total(self) -> int:
        return sum(self.model_dump().values())


class CaseEvaluation(StrictModel):
    config: BaselineName
    trial_id: str
    family: str
    base_template_id: str
    variant_id: str
    slices: list[str]
    reference_nonempty: StrictBool
    completed: Literal[True]
    schema_valid: Literal[True]
    exact_true_positives: Annotated[StrictInt, Field(ge=0)]
    prediction: ClinicalTrialEligibility
    evaluation: EvaluationReport
    errors: ErrorTaxonomy


class MeanFieldAccuracies(StrictModel):
    """Case-weighted means for all eight evaluator field accuracies."""

    category: Annotated[float, Field(ge=0.0, le=1.0)]
    concept: Annotated[float, Field(ge=0.0, le=1.0)]
    operator: Annotated[float, Field(ge=0.0, le=1.0)]
    value: Annotated[float, Field(ge=0.0, le=1.0)]
    unit: Annotated[float, Field(ge=0.0, le=1.0)]
    negated: Annotated[float, Field(ge=0.0, le=1.0)]
    temporal_relation: Annotated[float, Field(ge=0.0, le=1.0)]
    logic_connector: Annotated[float, Field(ge=0.0, le=1.0)]


class MetricAggregate(StrictModel):
    case_count: Annotated[StrictInt, Field(ge=0)]
    predicted_criteria: Annotated[StrictInt, Field(ge=0)]
    reference_criteria: Annotated[StrictInt, Field(ge=0)]
    criterion_text_true_positives: Annotated[StrictInt, Field(ge=0)]
    criterion_text_false_positives: Annotated[StrictInt, Field(ge=0)]
    criterion_text_false_negatives: Annotated[StrictInt, Field(ge=0)]
    micro_criterion_text_precision: Annotated[float, Field(ge=0.0, le=1.0)]
    micro_criterion_text_recall: Annotated[float, Field(ge=0.0, le=1.0)]
    micro_criterion_text_f1: Annotated[float, Field(ge=0.0, le=1.0)]
    mean_criterion_text_f1: Annotated[float, Field(ge=0.0, le=1.0)]
    mean_token_f1: Annotated[float, Field(ge=0.0, le=1.0)]
    mean_macro_field_accuracy: Annotated[float, Field(ge=0.0, le=1.0)]
    mean_field_accuracies: MeanFieldAccuracies
    criterion_text_perfect_trial_rate: Annotated[float, Field(ge=0.0, le=1.0)]


class ConfidenceInterval(StrictModel):
    estimate: float
    low: float
    high: float
    confidence: Annotated[float, Field(ge=0.95, le=0.95)] = 0.95
    resamples: Literal[10000] = 10000
    seed: Literal[20260901] = 20260901
    resampling_unit: Literal["case", "family"] = "case"
    cluster_count: Annotated[StrictInt, Field(gt=0)] | None = None
    interpretation: str


class TaxonomyRate(StrictModel):
    count: Annotated[StrictInt, Field(ge=0)]
    denominator: Annotated[StrictInt, Field(ge=0)]
    rate: Annotated[float, Field(ge=0.0, le=1.0)]
    denominator_basis: Literal["reference_criteria", "predicted_criteria", "aligned_pairs"]

    @model_validator(mode="after")
    def count_and_rate_match_denominator(self) -> TaxonomyRate:
        if self.count > self.denominator:
            raise ValueError("taxonomy count cannot exceed its denominator")
        expected = round(self.count / self.denominator, 6) if self.denominator else 0.0
        if self.rate != expected:
            raise ValueError("taxonomy rate must equal count / denominator")
        return self


class ErrorTaxonomySummary(StrictModel):
    raw_counts: ErrorTaxonomy
    aligned_pairs: Annotated[StrictInt, Field(ge=0)]
    metrics: dict[str, TaxonomyRate]
    overlap_note: str

    @model_validator(mode="after")
    def metrics_match_raw_counts(self) -> ErrorTaxonomySummary:
        if set(self.metrics) != set(ErrorTaxonomy.model_fields):
            raise ValueError("taxonomy metrics must cover every raw count")
        for name, count in self.raw_counts.model_dump().items():
            if self.metrics[name].count != count:
                raise ValueError("taxonomy metric counts must match raw counts")
        return self


class BaselineStatistics(StrictModel):
    config: BaselineName
    paid: Literal[False]
    network: Literal[False]
    input_tokens: Literal[0]
    output_tokens: Literal[0]
    estimated_cost_usd: Annotated[float, Field(ge=0.0, le=0.0)]
    completion_rate: Annotated[float, Field(ge=0.0, le=1.0)]
    schema_valid_rate: Annotated[float, Field(ge=0.0, le=1.0)]
    all_cases: MetricAggregate
    nonempty_reference_cases: MetricAggregate
    mean_metric_intervals: dict[str, ConfidenceInterval]
    family_cluster_mean_metric_intervals: dict[str, ConfidenceInterval]
    per_slice: dict[str, MetricAggregate]
    per_family: dict[str, MetricAggregate]
    leave_one_family_out: dict[str, MetricAggregate]
    taxonomy: ErrorTaxonomySummary


class PairedComparison(StrictModel):
    challenger: BaselineName
    reference: BaselineName
    delta_intervals: dict[str, ConfidenceInterval]
    family_cluster_delta_intervals: dict[str, ConfidenceInterval]
    limitation: str


class ExampleResult(StrictModel):
    trial_id: str
    family_id: str
    base_template_id: str
    variant_id: str
    slices: list[str]
    reference_criteria: StrictInt
    baseline_criterion_text_f1: dict[str, float]
    baseline_mismatch_event_totals: dict[str, StrictInt]


class DatasetCard(StrictModel):
    name: Literal["CriteriaBench Synthetic v0.1"]
    version: Literal["synthetic-v0.1"]
    manifest_sha256: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    case_count: Literal[80]
    family_count: StrictInt
    variants_per_family: StrictInt
    family_counts: dict[str, StrictInt]
    slice_counts: dict[str, StrictInt]
    base_template_count: StrictInt
    lineage_derivation: Literal["derived_from_manifest_family_and_record_order"]
    lineage: Annotated[list[CaseLineage], Field(min_length=80, max_length=80)]
    license: Literal["MIT"]
    annotation: AnnotationMetadata
    authoring_disclosure: str
    clinical_validation: Literal[False]


class SuiteReport(StrictModel):
    suite_version: Literal["offline-suite-v0.1.1"]
    analysis_contract_sha256: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    dataset: DatasetCard
    baselines: list[BaselineStatistics]
    paired_comparisons: list[PairedComparison]
    examples: Annotated[list[ExampleResult], Field(min_length=5, max_length=5)]
    reproducibility_command: str
    limitations: list[str]
