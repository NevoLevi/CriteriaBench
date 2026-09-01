"""Deterministic aggregates and paired bootstrap intervals."""

from __future__ import annotations

import hashlib
import json
import random
from collections.abc import Callable, Iterable, Sequence
from pathlib import Path
from typing import Literal

from criteriabench.suite.analysis import sum_taxonomies
from criteriabench.suite.models import (
    BaselineStatistics,
    CaseEvaluation,
    ConfidenceInterval,
    ErrorTaxonomy,
    ErrorTaxonomySummary,
    MeanFieldAccuracies,
    MetricAggregate,
    PairedComparison,
    TaxonomyRate,
)

BOOTSTRAP_RESAMPLES = 10_000
BOOTSTRAP_SEED = 20_260_901
CONFIDENCE_LEVEL = 0.95
CASE_RESAMPLING_INTERPRETATION = (
    "Fixed-suite case-resampling sensitivity only; not a population confidence interval."
)
FAMILY_CLUSTER_INTERPRETATION = (
    "Fixed-suite family-cluster sensitivity only; 10 constructed families cannot establish "
    "population, clinical, or external-validity uncertainty."
)
CASE_MIX_LIMITATION = (
    "Case-resampled and 10-family-cluster percentile intervals are descriptive sensitivity "
    "analyses for this fixed constructed suite, not population confidence intervals."
)
MEAN_METRICS: dict[str, Callable[[CaseEvaluation], float]] = {
    "mean_criterion_text_f1": lambda item: item.evaluation.exact_match_f1,
    "mean_token_f1": lambda item: item.evaluation.token_f1,
    "mean_macro_field_accuracy": lambda item: item.evaluation.macro_field_accuracy,
}
FIELD_METRICS: dict[str, Callable[[CaseEvaluation], float]] = {
    "category": lambda item: item.evaluation.category_accuracy,
    "concept": lambda item: item.evaluation.concept_accuracy,
    "operator": lambda item: item.evaluation.operator_accuracy,
    "value": lambda item: item.evaluation.value_accuracy,
    "unit": lambda item: item.evaluation.unit_accuracy,
    "negated": lambda item: item.evaluation.negated_accuracy,
    "temporal_relation": lambda item: item.evaluation.temporal_relation_accuracy,
    "logic_connector": lambda item: item.evaluation.logic_connector_accuracy,
}
ANALYSIS_CONTRACT = {
    "version": "offline-suite-analysis-v0.1.1",
    "dataset": "synthetic-v0.1",
    "evaluator": "criteriabench.evaluation.metrics.evaluate_extraction",
    "alignment": "evaluator deterministic optimal same-kind token alignment at 0.25",
    "criterion_text_counting": (
        "kind plus evaluator-normalized criterion text Counter multiset intersection"
    ),
    "execution": {
        "paid": False,
        "network": False,
        "input_tokens": 0,
        "output_tokens": 0,
        "estimated_cost_usd": 0.0,
    },
    "aggregates": [
        "micro_criterion_text_precision",
        "micro_criterion_text_recall",
        "micro_criterion_text_f1_from_aggregate_tp_fp_fn",
        *MEAN_METRICS,
        *FIELD_METRICS,
        "criterion_text_perfect_trial_rate",
        "completion_rate",
        "schema_valid_rate",
    ],
    "cohorts": [
        "all",
        "nonempty_reference",
        "per_slice",
        "per_family",
        "leave_one_family_out",
    ],
    "lineage": "family/base-template/variant IDs derived from manifest family and record order",
    "taxonomy": [
        "missing_criterion",
        "spurious_criterion",
        "text_mismatch",
        "category_mismatch",
        "concept_mismatch",
        "operator_mismatch",
        "value_mismatch",
        "unit_mismatch",
        "negation_mismatch",
        "temporal_relation_mismatch",
        "temporal_quantity_mismatch",
        "temporal_unit_mismatch",
        "temporal_reference_event_mismatch",
        "temporal_raw_text_mismatch",
        "logic_connector_mismatch",
        "logic_parent_mismatch",
        "evidence_quote_mismatch",
        "evidence_offset_mismatch",
    ],
    "taxonomy_denominators": {
        "missing_criterion": "reference_criteria",
        "spurious_criterion": "predicted_criteria",
        "all_mismatch_fields": "aligned_pairs",
    },
    "taxonomy_normalization": "evaluator normalization and stable-value serialization",
    "bootstrap": {
        "method": "percentile sensitivity",
        "units": ["case", "family_cluster"],
        "family_clusters": 10,
        "resamples": BOOTSTRAP_RESAMPLES,
        "seed": BOOTSTRAP_SEED,
        "confidence": CONFIDENCE_LEVEL,
        "rounding_decimals": 6,
        "population_claim": False,
    },
}


def analysis_contract_sha256() -> str:
    """Bind the declared contract and canonical-LF implementation source bytes."""

    suite_dir = Path(__file__).resolve().parent
    package_dir = suite_dir.parent
    sources = (
        ("src/criteriabench/suite/models.py", suite_dir / "models.py"),
        ("src/criteriabench/suite/loader.py", suite_dir / "loader.py"),
        ("src/criteriabench/suite/baselines.py", suite_dir / "baselines.py"),
        ("src/criteriabench/suite/analysis.py", suite_dir / "analysis.py"),
        ("src/criteriabench/suite/statistics.py", suite_dir / "statistics.py"),
        ("src/criteriabench/suite/reporting.py", suite_dir / "reporting.py"),
        ("src/criteriabench/suite/runner.py", suite_dir / "runner.py"),
        ("src/criteriabench/evaluation/metrics.py", package_dir / "evaluation" / "metrics.py"),
        ("src/criteriabench/domain/schemas.py", package_dir / "domain" / "schemas.py"),
    )
    digest = hashlib.sha256()
    declared = json.dumps(ANALYSIS_CONTRACT, sort_keys=True, separators=(",", ":"))
    digest.update((declared + "\n").encode("utf-8"))
    for label, path in sources:
        source = path.read_bytes().decode("utf-8-sig")
        canonical = source.replace("\r\n", "\n").replace("\r", "\n")
        digest.update(f"--- {label}\n".encode())
        digest.update(canonical.encode("utf-8"))
        if not canonical.endswith("\n"):
            digest.update(b"\n")
    return digest.hexdigest()


ANALYSIS_CONTRACT_SHA256 = analysis_contract_sha256()


def summarize_baseline(results: Sequence[CaseEvaluation]) -> BaselineStatistics:
    if not results:
        raise ValueError("a baseline summary requires at least one case")
    config = results[0].config
    if any(item.config != config for item in results):
        raise ValueError("baseline summary cannot mix configurations")
    nonempty = [item for item in results if item.reference_nonempty]
    slices = sorted({slice_name for item in results for slice_name in item.slices})
    families = sorted({item.family for item in results})
    return BaselineStatistics(
        config=config,
        paid=False,
        network=False,
        input_tokens=0,
        output_tokens=0,
        estimated_cost_usd=0.0,
        completion_rate=_rate(sum(item.completed for item in results), len(results)),
        schema_valid_rate=_rate(sum(item.schema_valid for item in results), len(results)),
        all_cases=aggregate_metrics(results),
        nonempty_reference_cases=aggregate_metrics(nonempty),
        mean_metric_intervals={
            name: percentile_interval([extractor(item) for item in results])
            for name, extractor in MEAN_METRICS.items()
        },
        family_cluster_mean_metric_intervals={
            name: family_cluster_interval([(item.family, extractor(item)) for item in results])
            for name, extractor in MEAN_METRICS.items()
        },
        per_slice={
            slice_name: aggregate_metrics([item for item in results if slice_name in item.slices])
            for slice_name in slices
        },
        per_family={
            family: aggregate_metrics([item for item in results if item.family == family])
            for family in families
        },
        leave_one_family_out={
            family: aggregate_metrics([item for item in results if item.family != family])
            for family in families
        },
        taxonomy=summarize_taxonomy(results),
    )


def aggregate_metrics(results: Sequence[CaseEvaluation]) -> MetricAggregate:
    case_count = len(results)
    predicted = sum(item.evaluation.predicted_count for item in results)
    reference = sum(item.evaluation.reference_count for item in results)
    true_positives = sum(item.exact_true_positives for item in results)
    if true_positives > predicted or true_positives > reference:
        raise ValueError("criterion-text true positives cannot exceed aggregate counts")
    false_positives = predicted - true_positives
    false_negatives = reference - true_positives
    both_empty = predicted == 0 and reference == 0
    precision = _ratio(
        true_positives,
        true_positives + false_positives,
        empty=1.0 if both_empty else 0.0,
    )
    recall = _ratio(
        true_positives,
        true_positives + false_negatives,
        empty=1.0 if both_empty else 0.0,
    )
    criterion_text_f1 = _ratio(
        2 * true_positives,
        2 * true_positives + false_positives + false_negatives,
        empty=1.0 if both_empty else 0.0,
    )
    return MetricAggregate(
        case_count=case_count,
        predicted_criteria=predicted,
        reference_criteria=reference,
        criterion_text_true_positives=true_positives,
        criterion_text_false_positives=false_positives,
        criterion_text_false_negatives=false_negatives,
        micro_criterion_text_precision=_six(precision),
        micro_criterion_text_recall=_six(recall),
        micro_criterion_text_f1=_six(criterion_text_f1),
        mean_criterion_text_f1=_mean([item.evaluation.exact_match_f1 for item in results]),
        mean_token_f1=_mean([item.evaluation.token_f1 for item in results]),
        mean_macro_field_accuracy=_mean([item.evaluation.macro_field_accuracy for item in results]),
        mean_field_accuracies=MeanFieldAccuracies(
            **{
                name: _mean(extractor(item) for item in results)
                for name, extractor in FIELD_METRICS.items()
            }
        ),
        criterion_text_perfect_trial_rate=_rate(
            sum(item.evaluation.exact_match_f1 == 1.0 for item in results), case_count
        ),
    )


def summarize_taxonomy(results: Sequence[CaseEvaluation]) -> ErrorTaxonomySummary:
    """Attach explicit evaluator-aligned denominators and rates to overlapping counts."""

    raw_counts = sum_taxonomies(item.errors for item in results)
    return summarize_taxonomy_totals(
        raw_counts,
        predicted_criteria=sum(item.evaluation.predicted_count for item in results),
        reference_criteria=sum(item.evaluation.reference_count for item in results),
    )


def summarize_taxonomy_totals(
    raw_counts: ErrorTaxonomy,
    *,
    predicted_criteria: int,
    reference_criteria: int,
) -> ErrorTaxonomySummary:
    """Build denominator-aware taxonomy metrics from already-aggregated counts."""

    aligned_from_reference = reference_criteria - raw_counts.missing_criterion
    aligned_from_prediction = predicted_criteria - raw_counts.spurious_criterion
    if aligned_from_reference != aligned_from_prediction or aligned_from_reference < 0:
        raise ValueError("taxonomy counts are inconsistent with evaluator alignment")
    aligned_pairs = aligned_from_reference
    metrics: dict[str, TaxonomyRate] = {}
    for name, count in raw_counts.model_dump().items():
        basis: Literal["reference_criteria", "predicted_criteria", "aligned_pairs"]
        if name == "missing_criterion":
            denominator = reference_criteria
            basis = "reference_criteria"
        elif name == "spurious_criterion":
            denominator = predicted_criteria
            basis = "predicted_criteria"
        else:
            denominator = aligned_pairs
            basis = "aligned_pairs"
        metrics[name] = TaxonomyRate(
            count=count,
            denominator=denominator,
            rate=_rate(count, denominator),
            denominator_basis=basis,
        )
    return ErrorTaxonomySummary(
        raw_counts=raw_counts,
        aligned_pairs=aligned_pairs,
        metrics=metrics,
        overlap_note=(
            "Mismatch categories can overlap on the same aligned criterion; raw counts must not "
            "be summed as a count of unique erroneous criteria or cases."
        ),
    )


def compare_paired(
    challenger: Sequence[CaseEvaluation],
    reference: Sequence[CaseEvaluation],
) -> PairedComparison:
    if not challenger or not reference or len(challenger) != len(reference):
        raise ValueError("paired comparison requires equal non-empty result sets")
    challenger_by_id = {item.trial_id: item for item in challenger}
    reference_by_id = {item.trial_id: item for item in reference}
    if (
        len(challenger_by_id) != len(challenger)
        or challenger_by_id.keys() != reference_by_id.keys()
    ):
        raise ValueError("paired comparison requires the same unique trial IDs")
    ordered_ids = sorted(challenger_by_id)
    if any(
        (
            challenger_by_id[trial_id].family,
            challenger_by_id[trial_id].base_template_id,
            challenger_by_id[trial_id].variant_id,
        )
        != (
            reference_by_id[trial_id].family,
            reference_by_id[trial_id].base_template_id,
            reference_by_id[trial_id].variant_id,
        )
        for trial_id in ordered_ids
    ):
        raise ValueError("paired comparison requires identical case lineage")
    intervals: dict[str, ConfidenceInterval] = {}
    family_cluster_intervals: dict[str, ConfidenceInterval] = {}
    for name, extractor in MEAN_METRICS.items():
        deltas = [
            extractor(challenger_by_id[trial_id]) - extractor(reference_by_id[trial_id])
            for trial_id in ordered_ids
        ]
        intervals[name] = percentile_interval(deltas)
        family_cluster_intervals[name] = family_cluster_interval(
            [
                (challenger_by_id[trial_id].family, delta)
                for trial_id, delta in zip(ordered_ids, deltas, strict=True)
            ]
        )
    return PairedComparison(
        challenger=challenger[0].config,
        reference=reference[0].config,
        delta_intervals=intervals,
        family_cluster_delta_intervals=family_cluster_intervals,
        limitation=CASE_MIX_LIMITATION,
    )


def percentile_interval(values: Sequence[float]) -> ConfidenceInterval:
    if not values:
        return ConfidenceInterval(
            estimate=0.0,
            low=0.0,
            high=0.0,
            interpretation=CASE_RESAMPLING_INTERPRETATION,
        )
    generator = random.Random(BOOTSTRAP_SEED)
    length = len(values)
    resampled_means = [
        sum(values[generator.randrange(length)] for _ in range(length)) / length
        for _ in range(BOOTSTRAP_RESAMPLES)
    ]
    resampled_means.sort()
    tail = (1.0 - CONFIDENCE_LEVEL) / 2.0
    return ConfidenceInterval(
        estimate=_mean(values),
        low=_six(_percentile(resampled_means, tail)),
        high=_six(_percentile(resampled_means, 1.0 - tail)),
        interpretation=CASE_RESAMPLING_INTERPRETATION,
    )


def family_cluster_interval(
    values: Sequence[tuple[str, float]],
) -> ConfidenceInterval:
    """Resample whole fixed-suite families while keeping their variants together."""

    if not values:
        raise ValueError("family-cluster sensitivity requires at least one value")
    clustered: dict[str, list[float]] = {}
    for family, value in values:
        clustered.setdefault(family, []).append(value)
    families = sorted(clustered)
    generator = random.Random(BOOTSTRAP_SEED)
    resampled_means: list[float] = []
    for _ in range(BOOTSTRAP_RESAMPLES):
        sampled_families = [
            families[generator.randrange(len(families))] for _ in range(len(families))
        ]
        sampled_values = [value for family in sampled_families for value in clustered[family]]
        resampled_means.append(sum(sampled_values) / len(sampled_values))
    resampled_means.sort()
    tail = (1.0 - CONFIDENCE_LEVEL) / 2.0
    return ConfidenceInterval(
        estimate=_mean(value for _, value in values),
        low=_six(_percentile(resampled_means, tail)),
        high=_six(_percentile(resampled_means, 1.0 - tail)),
        resampling_unit="family",
        cluster_count=len(families),
        interpretation=FAMILY_CLUSTER_INTERPRETATION,
    )


def _percentile(sorted_values: Sequence[float], probability: float) -> float:
    position = probability * (len(sorted_values) - 1)
    lower = int(position)
    upper = min(lower + 1, len(sorted_values) - 1)
    fraction = position - lower
    return sorted_values[lower] * (1.0 - fraction) + sorted_values[upper] * fraction


def _mean(values: Iterable[float]) -> float:
    materialized = list(values)
    return _six(sum(materialized) / len(materialized)) if materialized else 0.0


def _rate(numerator: int, denominator: int) -> float:
    return _six(_ratio(numerator, denominator))


def _ratio(numerator: int, denominator: int, *, empty: float = 0.0) -> float:
    return numerator / denominator if denominator else empty


def _harmonic_mean(precision: float, recall: float) -> float:
    return 2.0 * precision * recall / (precision + recall) if precision + recall else 0.0


def _six(value: float) -> float:
    return round(value, 6)
