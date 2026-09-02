"""Deterministic whole-trial cluster bootstrap for Real v1 metrics."""

from __future__ import annotations

import random
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

from criteriabench.real_eval.metrics import MatchCounts
from criteriabench.real_eval.models import ClusterInterval

BootstrapMetric = Literal["semantic_graph_f1", "ast_exact_match_accuracy"]


@dataclass(frozen=True, slots=True)
class ClusterObservation:
    case_id: str
    trial_id: str
    ast_exact_match: bool
    semantic_graph: MatchCounts


def trial_cluster_interval(
    observations: Sequence[ClusterObservation],
    metric: BootstrapMetric,
    *,
    resamples: int,
    seed: int,
) -> ClusterInterval:
    """Resample NCT IDs with replacement and keep every criterion in a sampled trial."""

    _validate_bootstrap_request(metric, resamples)
    clusters = _clusters(observations)
    rng = random.Random(seed)
    draws = [
        _metric(
            [item for index in _draw_indices(rng, len(clusters)) for item in clusters[index]],
            metric,
        )
        for _ in range(resamples)
    ]
    return ClusterInterval(
        estimate=_six(_metric(list(observations), metric)),
        low=_six(_percentile(draws, 0.025)),
        high=_six(_percentile(draws, 0.975)),
        confidence=0.95,
        resamples=resamples,
        seed=seed,
        resampling_unit="trial_id",
        cluster_count=len(clusters),
    )


def paired_trial_cluster_delta_interval(
    challenger: Sequence[ClusterObservation],
    reference: Sequence[ClusterObservation],
    metric: BootstrapMetric,
    *,
    resamples: int,
    seed: int,
) -> ClusterInterval:
    """Paired resampling uses the same NCT draw for both frozen systems."""

    _validate_bootstrap_request(metric, resamples)
    if not challenger or not reference:
        raise ValueError("paired cluster bootstrap requires non-empty observations")
    if len({item.case_id for item in challenger}) != len(challenger):
        raise ValueError("challenger observations contain duplicate case IDs")
    if len({item.case_id for item in reference}) != len(reference):
        raise ValueError("reference observations contain duplicate case IDs")
    challenger_by_id = {item.case_id: item for item in challenger}
    reference_by_id = {item.case_id: item for item in reference}
    if challenger_by_id.keys() != reference_by_id.keys():
        raise ValueError("paired observations must contain identical case IDs")
    ordered_ids = sorted(challenger_by_id)
    for case_id in ordered_ids:
        if challenger_by_id[case_id].trial_id != reference_by_id[case_id].trial_id:
            raise ValueError("paired observations disagree on trial ID")

    paired_clusters: dict[str, list[str]] = defaultdict(list)
    for case_id in ordered_ids:
        paired_clusters[challenger_by_id[case_id].trial_id].append(case_id)
    clusters = [paired_clusters[key] for key in sorted(paired_clusters)]
    rng = random.Random(seed)
    draws: list[float] = []
    for _ in range(resamples):
        sampled_ids = [
            case_id for index in _draw_indices(rng, len(clusters)) for case_id in clusters[index]
        ]
        challenger_sample = [challenger_by_id[case_id] for case_id in sampled_ids]
        reference_sample = [reference_by_id[case_id] for case_id in sampled_ids]
        draws.append(_metric(challenger_sample, metric) - _metric(reference_sample, metric))
    estimate = _metric(list(challenger), metric) - _metric(list(reference), metric)
    return ClusterInterval(
        estimate=_six(estimate),
        low=_six(_percentile(draws, 0.025)),
        high=_six(_percentile(draws, 0.975)),
        confidence=0.95,
        resamples=resamples,
        seed=seed,
        resampling_unit="trial_id",
        cluster_count=len(clusters),
    )


def _clusters(observations: Sequence[ClusterObservation]) -> list[list[ClusterObservation]]:
    if not observations:
        raise ValueError("cluster bootstrap requires at least one observation")
    grouped: dict[str, list[ClusterObservation]] = defaultdict(list)
    seen: set[str] = set()
    for item in observations:
        if item.case_id in seen:
            raise ValueError("cluster observations contain duplicate case IDs")
        seen.add(item.case_id)
        grouped[item.trial_id].append(item)
    return [grouped[key] for key in sorted(grouped)]


def _draw_indices(rng: random.Random, count: int) -> list[int]:
    return [rng.randrange(count) for _ in range(count)]


def _validate_bootstrap_request(metric: BootstrapMetric, resamples: int) -> None:
    if metric not in {"semantic_graph_f1", "ast_exact_match_accuracy"}:
        raise ValueError("unsupported bootstrap metric")
    if resamples < 1:
        raise ValueError("bootstrap resamples must be positive")


def _metric(observations: Sequence[ClusterObservation], metric: BootstrapMetric) -> float:
    if metric == "ast_exact_match_accuracy":
        return sum(item.ast_exact_match for item in observations) / len(observations)
    counts = MatchCounts(0, 0, 0)
    for item in observations:
        counts += item.semantic_graph
    return counts.f1


def _percentile(values: Sequence[float], probability: float) -> float:
    ordered = sorted(values)
    if not ordered:
        raise ValueError("percentile requires at least one value")
    position = (len(ordered) - 1) * probability
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    fraction = position - lower
    return ordered[lower] * (1.0 - fraction) + ordered[upper] * fraction


def _six(value: float) -> float:
    return round(value, 6)
