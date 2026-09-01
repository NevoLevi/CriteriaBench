"""Deterministic offline scoring for already-frozen model predictions."""

from __future__ import annotations

from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal

from criteriabench.domain.schemas import ClinicalTrialEligibility
from criteriabench.evaluation.metrics import EvaluationReport, evaluate_extraction
from criteriabench.suite.analysis import (
    classify_errors,
    count_exact_true_positives,
    sum_taxonomies,
)
from criteriabench.suite.models import (
    ErrorTaxonomy,
    LoadedSuite,
    MeanFieldAccuracies,
    MetricAggregate,
)
from criteriabench.suite.statistics import summarize_taxonomy_totals

from .models import (
    REPORT_SCHEMA_VERSION,
    CasePrediction,
    CaseScore,
    CompletedPrediction,
    FailedPrediction,
    PredictionBundle,
    PredictionScoreReport,
    UsageSummary,
)


@dataclass(frozen=True, slots=True)
class _EvaluatedCase:
    case: CasePrediction
    exact_true_positives: int
    evaluation: EvaluationReport
    errors: ErrorTaxonomy


def score_verified_bundle(
    bundle: PredictionBundle,
    loaded: LoadedSuite,
) -> PredictionScoreReport:
    """Score a bundle already checked by ``load_verified_bundle``.

    Failed cases receive zero for every quality metric in the primary cohort. The
    completed-only cohort is deliberately labelled diagnostic because omitting failed
    requests otherwise inflates model quality.
    """

    if len(bundle.cases) != len(loaded.cases):
        raise ValueError("verified bundle and loaded suite case counts differ")

    evaluated: list[_EvaluatedCase] = []
    for case, loaded_case in zip(bundle.cases, loaded.cases, strict=True):
        reference = loaded_case.fixture.reference
        if isinstance(case, CompletedPrediction):
            evaluation = evaluate_extraction(case.prediction, reference)
            exact_true_positives = count_exact_true_positives(case.prediction, reference)
            errors = classify_errors(case.prediction, reference)
        else:
            empty_prediction = ClinicalTrialEligibility(
                schema_version="1.0",
                trial_id=reference.trial_id,
                inclusion_criteria=[],
                exclusion_criteria=[],
                ambiguities=[],
            )
            reference_count = len(reference.inclusion_criteria) + len(reference.exclusion_criteria)
            evaluation = _failed_evaluation(reference_count)
            exact_true_positives = 0
            errors = classify_errors(empty_prediction, reference)
        evaluated.append(
            _EvaluatedCase(
                case=case,
                exact_true_positives=exact_true_positives,
                evaluation=evaluation,
                errors=errors,
            )
        )

    completed = [item for item in evaluated if isinstance(item.case, CompletedPrediction)]
    failed = [item for item in evaluated if isinstance(item.case, FailedPrediction)]
    failure_counts = Counter(
        item.case.failure.kind for item in failed if isinstance(item.case, FailedPrediction)
    )
    case_count = len(evaluated)
    primary = _aggregate(evaluated)
    taxonomy = summarize_taxonomy_totals(
        sum_taxonomies(item.errors for item in evaluated),
        predicted_criteria=primary.predicted_criteria,
        reference_criteria=primary.reference_criteria,
    )
    return PredictionScoreReport(
        schema_version=REPORT_SCHEMA_VERSION,
        scoring_contract="failures-score-zero-v1",
        bundle_sha256=bundle.bundle_sha256,
        dataset=bundle.dataset,
        run=bundle.run,
        completion_rate=_rate(len(completed), case_count),
        schema_valid_rate=_rate(len(completed), case_count),
        completed_cases=len(completed),
        failed_cases=len(failed),
        primary_all_cases=primary,
        completed_only_diagnostic=_aggregate(completed) if completed else None,
        failure_counts=dict(sorted(failure_counts.items())),
        taxonomy=taxonomy,
        usage=_summarize_usage(bundle.cases),
        cases=[_case_score(item) for item in evaluated],
    )


def _failed_evaluation(reference_count: int) -> EvaluationReport:
    return EvaluationReport(
        schema_valid=False,
        exact_match_precision=0.0,
        exact_match_recall=0.0,
        exact_match_f1=0.0,
        token_f1=0.0,
        category_accuracy=0.0,
        concept_accuracy=0.0,
        operator_accuracy=0.0,
        value_accuracy=0.0,
        unit_accuracy=0.0,
        negated_accuracy=0.0,
        temporal_relation_accuracy=0.0,
        logic_connector_accuracy=0.0,
        macro_field_accuracy=0.0,
        predicted_count=0,
        reference_count=reference_count,
    )


def _aggregate(items: Sequence[_EvaluatedCase]) -> MetricAggregate:
    case_count = len(items)
    predicted = sum(item.evaluation.predicted_count for item in items)
    reference = sum(item.evaluation.reference_count for item in items)
    true_positives = sum(item.exact_true_positives for item in items)
    both_empty_and_perfect = (
        case_count > 0
        and predicted == 0
        and reference == 0
        and all(item.evaluation.exact_match_f1 == 1.0 for item in items)
    )
    empty_value = 1.0 if both_empty_and_perfect else 0.0
    precision = _ratio(true_positives, predicted, empty=empty_value)
    recall = _ratio(true_positives, reference, empty=empty_value)
    micro_f1 = _harmonic_mean(precision, recall)

    def mean(field: str) -> float:
        return _mean([float(getattr(item.evaluation, field)) for item in items])

    return MetricAggregate(
        case_count=case_count,
        predicted_criteria=predicted,
        reference_criteria=reference,
        criterion_text_true_positives=true_positives,
        criterion_text_false_positives=predicted - true_positives,
        criterion_text_false_negatives=reference - true_positives,
        micro_criterion_text_precision=_six(precision),
        micro_criterion_text_recall=_six(recall),
        micro_criterion_text_f1=_six(micro_f1),
        mean_criterion_text_f1=mean("exact_match_f1"),
        mean_token_f1=mean("token_f1"),
        mean_macro_field_accuracy=mean("macro_field_accuracy"),
        mean_field_accuracies=MeanFieldAccuracies(
            category=mean("category_accuracy"),
            concept=mean("concept_accuracy"),
            operator=mean("operator_accuracy"),
            value=mean("value_accuracy"),
            unit=mean("unit_accuracy"),
            negated=mean("negated_accuracy"),
            temporal_relation=mean("temporal_relation_accuracy"),
            logic_connector=mean("logic_connector_accuracy"),
        ),
        criterion_text_perfect_trial_rate=_rate(
            sum(item.evaluation.exact_match_f1 == 1.0 for item in items),
            case_count,
        ),
    )


def _summarize_usage(cases: Sequence[CasePrediction]) -> UsageSummary:
    input_cost = sum((Decimal(case.usage.cost.input_cost_usd) for case in cases), Decimal(0))
    output_cost = sum((Decimal(case.usage.cost.output_cost_usd) for case in cases), Decimal(0))
    total_cost = sum((Decimal(case.usage.cost.total_cost_usd) for case in cases), Decimal(0))
    total_latency = sum(case.latency_ms for case in cases)
    observed_case_count = sum(case.usage.availability == "observed" for case in cases)
    unavailable_case_count = len(cases) - observed_case_count
    return UsageSummary(
        attempt_scope="all_attempts_including_retries",
        observed_case_count=observed_case_count,
        unavailable_case_count=unavailable_case_count,
        completeness=_rate(observed_case_count, len(cases)),
        monetary_totals_are_lower_bounds=unavailable_case_count > 0,
        input_tokens=sum(case.usage.tokens.input_tokens for case in cases),
        output_tokens=sum(case.usage.tokens.output_tokens for case in cases),
        total_tokens=sum(case.usage.tokens.total_tokens for case in cases),
        input_cost_usd=_usd(input_cost),
        output_cost_usd=_usd(output_cost),
        total_cost_usd=_usd(total_cost),
        total_latency_ms=total_latency,
        mean_latency_ms=_six(_ratio(total_latency, len(cases))),
        total_retries=sum(case.retries for case in cases),
    )


def _case_score(item: _EvaluatedCase) -> CaseScore:
    failure_kind = item.case.failure.kind if isinstance(item.case, FailedPrediction) else None
    return CaseScore(
        case_path=item.case.case_path,
        trial_id=item.case.trial_id,
        status=item.case.status,
        failure_kind=failure_kind,
        criterion_text_true_positives=item.exact_true_positives,
        criterion_text_f1=item.evaluation.exact_match_f1,
        token_f1=item.evaluation.token_f1,
        macro_field_accuracy=item.evaluation.macro_field_accuracy,
    )


def _usd(value: Decimal) -> str:
    return format(value, ".9f")


def _mean(values: Sequence[float]) -> float:
    return _six(sum(values) / len(values)) if values else 0.0


def _rate(numerator: int, denominator: int) -> float:
    return _six(_ratio(numerator, denominator))


def _ratio(numerator: int | float, denominator: int, *, empty: float = 0.0) -> float:
    return numerator / denominator if denominator else empty


def _harmonic_mean(precision: float, recall: float) -> float:
    return 2.0 * precision * recall / (precision + recall) if precision + recall else 0.0


def _six(value: float) -> float:
    return round(value, 6)
