"""Deterministic aggregates and paired bootstrap intervals."""

from __future__ import annotations

import hashlib
import json
import random
from collections.abc import Callable, Iterable, Sequence
from pathlib import Path

from criteriabench.suite.analysis import sum_taxonomies
from criteriabench.suite.models import (
    BaselineStatistics,
    CaseEvaluation,
    ConfidenceInterval,
    MetricAggregate,
    PairedComparison,
)

BOOTSTRAP_RESAMPLES = 10_000
BOOTSTRAP_SEED = 20_260_901
CONFIDENCE_LEVEL = 0.95
CASE_MIX_LIMITATION = (
    "Percentile intervals quantify uncertainty only for this constructed 80-case mix; "
    "they do not establish population, clinical, or external-validity uncertainty."
)
MEAN_METRICS: dict[str, Callable[[CaseEvaluation], float]] = {
    "mean_exact_f1": lambda item: item.evaluation.exact_match_f1,
    "mean_token_f1": lambda item: item.evaluation.token_f1,
    "mean_macro_field_accuracy": lambda item: item.evaluation.macro_field_accuracy,
}
ANALYSIS_CONTRACT = {
    "version": "offline-suite-analysis-v0.1",
    "dataset": "synthetic-v0.1",
    "evaluator": "criteriabench.evaluation.metrics.evaluate_extraction",
    "alignment": "evaluator deterministic optimal same-kind token alignment at 0.25",
    "exact_counting": "criterion-key Counter multiset intersection",
    "execution": {
        "paid": False,
        "network": False,
        "input_tokens": 0,
        "output_tokens": 0,
        "estimated_cost_usd": 0.0,
    },
    "aggregates": [
        "micro_exact_precision",
        "micro_exact_recall",
        "micro_exact_f1",
        *MEAN_METRICS,
        "trial_perfect_rate",
        "completion_rate",
        "schema_valid_rate",
    ],
    "cohorts": ["all", "nonempty_gold", "per_slice"],
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
    "bootstrap": {
        "method": "paired percentile",
        "resamples": BOOTSTRAP_RESAMPLES,
        "seed": BOOTSTRAP_SEED,
        "confidence": CONFIDENCE_LEVEL,
        "rounding_decimals": 6,
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
        nonempty_gold_cases=aggregate_metrics(nonempty),
        mean_metric_intervals={
            name: percentile_interval([extractor(item) for item in results])
            for name, extractor in MEAN_METRICS.items()
        },
        per_slice={
            slice_name: aggregate_metrics([item for item in results if slice_name in item.slices])
            for slice_name in slices
        },
        taxonomy=sum_taxonomies(item.errors for item in results),
    )


def aggregate_metrics(results: Sequence[CaseEvaluation]) -> MetricAggregate:
    case_count = len(results)
    predicted = sum(item.evaluation.predicted_count for item in results)
    reference = sum(item.evaluation.reference_count for item in results)
    true_positives = sum(item.exact_true_positives for item in results)
    both_empty = predicted == 0 and reference == 0
    precision = _ratio(true_positives, predicted, empty=1.0 if both_empty else 0.0)
    recall = _ratio(true_positives, reference, empty=1.0 if both_empty else 0.0)
    exact_f1 = _harmonic_mean(precision, recall)
    return MetricAggregate(
        case_count=case_count,
        predicted_criteria=predicted,
        reference_criteria=reference,
        exact_true_positives=true_positives,
        micro_exact_precision=_six(precision),
        micro_exact_recall=_six(recall),
        micro_exact_f1=_six(exact_f1),
        mean_exact_f1=_mean([item.evaluation.exact_match_f1 for item in results]),
        mean_token_f1=_mean([item.evaluation.token_f1 for item in results]),
        mean_macro_field_accuracy=_mean([item.evaluation.macro_field_accuracy for item in results]),
        trial_perfect_rate=_rate(
            sum(item.evaluation.exact_match_f1 == 1.0 for item in results), case_count
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
    intervals: dict[str, ConfidenceInterval] = {}
    for name, extractor in MEAN_METRICS.items():
        deltas = [
            extractor(challenger_by_id[trial_id]) - extractor(reference_by_id[trial_id])
            for trial_id in ordered_ids
        ]
        intervals[name] = percentile_interval(deltas)
    return PairedComparison(
        challenger=challenger[0].config,
        reference=reference[0].config,
        delta_intervals=intervals,
        limitation=CASE_MIX_LIMITATION,
    )


def percentile_interval(values: Sequence[float]) -> ConfidenceInterval:
    if not values:
        return ConfidenceInterval(estimate=0.0, low=0.0, high=0.0)
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
