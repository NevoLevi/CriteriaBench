"""Pure offline scoring and deterministic replay for sealed Real v1 bundles."""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Sequence
from decimal import Decimal

from criteriabench.real_eval.bootstrap import ClusterObservation, trial_cluster_interval
from criteriabench.real_eval.integrity import (
    canonical_sha256,
    validate_reference_cases,
    verify_bundle,
    verify_protocol,
)
from criteriabench.real_eval.metrics import (
    GraphComparison,
    MatchCounts,
    compare_graphs,
    failed_graph_comparison,
)
from criteriabench.real_eval.models import (
    REAL_GRAPH_SCORING_V1_SHA256,
    REPORT_SCHEMA_VERSION,
    CaseScore,
    ClusterInterval,
    CompletedPrediction,
    FailedPrediction,
    FrozenProtocol,
    MatchCountsModel,
    MetricAggregate,
    PredictionBundle,
    PredictionScoreReport,
    ReferenceCase,
    TokenCounts,
    UsagePricedCost,
    UsageSummary,
    usd,
)

SCORING_CONTRACT = {
    "version": "real-graph-scoring-v1",
    "primary": "micro_f1_over_canonical_semantic_nodes_plus_edges",
    "failed_case_policy": "empty_prediction_all_reference_components_false_negative",
    "missing_upstream_reference_policy": (
        "operational_denominator_only_semantic_denominator_disclosed"
    ),
    "bootstrap": "percentile_resampling_of_whole_trial_id_clusters",
    "rounding_decimals": 6,
}
SCORING_CONTRACT_SHA256 = canonical_sha256(SCORING_CONTRACT)
if SCORING_CONTRACT_SHA256 != REAL_GRAPH_SCORING_V1_SHA256:
    raise RuntimeError("Real graph scoring contract digest is stale")


def score_bundle(
    bundle: PredictionBundle,
    references: Sequence[ReferenceCase],
    protocol: FrozenProtocol,
) -> PredictionScoreReport:
    """Replay a sealed bundle without provider imports, credentials, or network access."""

    verify_protocol(protocol)
    if bundle.run.protocol_sha256 != protocol.protocol_sha256:
        raise ValueError("bundle run does not match frozen evaluation protocol")
    if bundle.dataset != protocol.dataset:
        raise ValueError("bundle dataset does not match frozen evaluation protocol")
    validate_reference_cases(references)
    verify_bundle(bundle, references)
    if any(case.split != bundle.dataset.split for case in references):
        raise ValueError("reference split does not match dataset binding")
    available = sum(case.reference is not None for case in references)
    if bundle.dataset.scorable_case_count != available:
        raise ValueError("dataset scorable case count does not match references")

    scores: list[CaseScore] = []
    comparisons: list[GraphComparison] = []
    completed_comparisons: list[GraphComparison] = []
    observations: list[ClusterObservation] = []
    failures: Counter[str] = Counter()

    for prediction, case in zip(bundle.cases, references, strict=True):
        failure_kind = prediction.failure.kind if isinstance(prediction, FailedPrediction) else None
        if failure_kind is not None:
            failures[failure_kind] += 1
        reference = case.reference
        if reference is None:
            scores.append(
                CaseScore(
                    case_id=case.case_id,
                    trial_id=case.trial_id,
                    status="unscorable",
                    failure_kind=failure_kind,
                    ast_exact_match=None,
                    semantic_graph=None,
                    nodes=None,
                    edges=None,
                    predicates=None,
                    concept_evidence=None,
                )
            )
            continue

        comparison = (
            compare_graphs(prediction.prediction, reference)
            if isinstance(prediction, CompletedPrediction)
            else failed_graph_comparison(reference)
        )
        comparisons.append(comparison)
        if isinstance(prediction, CompletedPrediction):
            completed_comparisons.append(comparison)
        observations.append(
            ClusterObservation(
                case_id=case.case_id,
                trial_id=case.trial_id,
                ast_exact_match=comparison.ast_exact_match,
                semantic_graph=comparison.semantic_graph,
            )
        )
        scores.append(
            CaseScore(
                case_id=case.case_id,
                trial_id=case.trial_id,
                status=prediction.status,
                failure_kind=failure_kind,
                ast_exact_match=comparison.ast_exact_match,
                semantic_graph=_counts_model(comparison.semantic_graph),
                nodes=_counts_model(comparison.nodes),
                edges=_counts_model(comparison.edges),
                predicates=_counts_model(comparison.predicates),
                concept_evidence=_counts_model(comparison.concept_evidence),
            )
        )

    completed_count = sum(isinstance(item, CompletedPrediction) for item in bundle.cases)
    intervals: dict[str, ClusterInterval] = {}
    if observations:
        for metric in ("semantic_graph_f1", "ast_exact_match_accuracy"):
            intervals[metric] = trial_cluster_interval(
                observations,
                metric,
                resamples=protocol.bootstrap_resamples,
                seed=protocol.bootstrap_seed,
            )
    return PredictionScoreReport(
        schema_version=REPORT_SCHEMA_VERSION,
        scoring_contract_sha256=SCORING_CONTRACT_SHA256,
        bundle_sha256=bundle.bundle_sha256,
        dataset=bundle.dataset,
        run=bundle.run,
        operational_case_count=len(bundle.cases),
        scorable_case_count=len(comparisons),
        unscorable_reference_count=len(bundle.cases) - len(comparisons),
        completed_cases=completed_count,
        failed_cases=len(bundle.cases) - completed_count,
        completion_rate=_six(completed_count / len(bundle.cases)),
        failure_counts=dict(sorted(failures.items())),
        primary_all_scorable=_aggregate(comparisons),
        completed_only_diagnostic=(
            _aggregate(completed_comparisons) if completed_comparisons else None
        ),
        trial_cluster_intervals=intervals,
        usage=_usage_summary(bundle),
        cases=scores,
    )


def _aggregate(items: Sequence[GraphComparison]) -> MetricAggregate:
    nodes = _sum_counts(item.nodes for item in items)
    edges = _sum_counts(item.edges for item in items)
    predicates = _sum_counts(item.predicates for item in items)
    concept_evidence = _sum_counts(item.concept_evidence for item in items)
    semantic = nodes + edges
    exact = sum(item.ast_exact_match for item in items)
    return MetricAggregate(
        scorable_case_count=len(items),
        ast_exact_match_count=exact,
        ast_exact_match_accuracy=_six(exact / len(items)) if items else 0.0,
        semantic_graph=_counts_model(semantic),
        nodes=_counts_model(nodes),
        edges=_counts_model(edges),
        predicates=_counts_model(predicates),
        concept_evidence=_counts_model(concept_evidence),
    )


def _sum_counts(items: Iterable[MatchCounts]) -> MatchCounts:
    total = MatchCounts(0, 0, 0)
    for item in items:
        total += item
    return total


def _counts_model(counts: MatchCounts) -> MatchCountsModel:
    return MatchCountsModel(
        true_positive=counts.true_positive,
        false_positive=counts.false_positive,
        false_negative=counts.false_negative,
        precision=_six(counts.precision),
        recall=_six(counts.recall),
        f1=_six(counts.f1),
    )


def _usage_summary(bundle: PredictionBundle) -> UsageSummary:
    cases = bundle.cases
    latencies = sorted(case.total_latency_ms for case in cases)
    input_tokens = sum(case.usage.tokens.input_tokens for case in cases)
    output_tokens = sum(case.usage.tokens.output_tokens for case in cases)
    input_cost = sum((Decimal(case.usage.cost.input_cost_usd) for case in cases), Decimal(0))
    output_cost = sum((Decimal(case.usage.cost.output_cost_usd) for case in cases), Decimal(0))
    total_attempts = sum(case.usage.total_attempts for case in cases)
    complete = sum(case.usage.availability == "complete" for case in cases)
    partial = sum(case.usage.availability == "partial" for case in cases)
    unavailable = len(cases) - complete - partial
    return UsageSummary(
        case_count=len(cases),
        complete_case_count=complete,
        partial_case_count=partial,
        unavailable_case_count=unavailable,
        monetary_totals_are_lower_bounds=partial + unavailable > 0,
        tokens=TokenCounts(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            total_tokens=input_tokens + output_tokens,
        ),
        cost=UsagePricedCost(
            input_cost_usd=usd(input_cost),
            output_cost_usd=usd(output_cost),
            total_cost_usd=usd(input_cost + output_cost),
        ),
        total_latency_ms=sum(latencies),
        p50_latency_ms=_percentile(latencies, 0.50),
        p95_latency_ms=_percentile(latencies, 0.95),
        total_attempts=total_attempts,
        total_retries=total_attempts - len(cases),
    )


def _percentile(values: Sequence[int], probability: float) -> float:
    position = (len(values) - 1) * probability
    lower = int(position)
    upper = min(lower + 1, len(values) - 1)
    fraction = position - lower
    return _six(values[lower] * (1.0 - fraction) + values[upper] * fraction)


def _six(value: float) -> float:
    return round(value, 6)
