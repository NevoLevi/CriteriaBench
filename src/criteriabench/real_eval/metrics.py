"""Deterministic structural metrics for canonical ``EligibilityGraphV2`` trees."""

from __future__ import annotations

import json
from collections import Counter
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from typing import Any

from criteriabench.real.graph_v2 import (
    AllOf,
    AnyOf,
    AtLeast,
    EligibilityGraphV2,
    Expression,
    Not,
    Predicate,
    canonical_graph_sha256,
    canonicalize_graph,
)

SignatureCounter = Counter[str]


@dataclass(frozen=True, slots=True)
class MatchCounts:
    """Multiset match counts from which precision, recall, and F1 are derived."""

    true_positive: int
    false_positive: int
    false_negative: int

    @property
    def precision(self) -> float:
        return _ratio(self.true_positive, self.true_positive + self.false_positive)

    @property
    def recall(self) -> float:
        return _ratio(self.true_positive, self.true_positive + self.false_negative)

    @property
    def f1(self) -> float:
        denominator = 2 * self.true_positive + self.false_positive + self.false_negative
        return _ratio(2 * self.true_positive, denominator)

    def __add__(self, other: MatchCounts) -> MatchCounts:
        return MatchCounts(
            true_positive=self.true_positive + other.true_positive,
            false_positive=self.false_positive + other.false_positive,
            false_negative=self.false_negative + other.false_negative,
        )


@dataclass(frozen=True, slots=True)
class GraphComparison:
    """One reference/prediction comparison without any provider-side state."""

    ast_exact_match: bool
    nodes: MatchCounts
    edges: MatchCounts
    predicates: MatchCounts
    concept_evidence: MatchCounts

    @property
    def semantic_graph(self) -> MatchCounts:
        """Primary semantic count pool: canonical nodes plus canonical edges."""

        return self.nodes + self.edges


@dataclass(frozen=True, slots=True)
class GraphComponents:
    nodes: SignatureCounter
    edges: SignatureCounter
    predicates: SignatureCounter
    concept_evidence: SignatureCounter


def compare_graphs(
    prediction: EligibilityGraphV2,
    reference: EligibilityGraphV2,
) -> GraphComparison:
    """Compare two graphs after only semantics-preserving canonicalization."""

    predicted = graph_components(prediction)
    expected = graph_components(reference)
    return GraphComparison(
        ast_exact_match=canonical_graph_sha256(prediction) == canonical_graph_sha256(reference),
        nodes=_match_counts(predicted.nodes, expected.nodes),
        edges=_match_counts(predicted.edges, expected.edges),
        predicates=_match_counts(predicted.predicates, expected.predicates),
        concept_evidence=_match_counts(predicted.concept_evidence, expected.concept_evidence),
    )


def failed_graph_comparison(reference: EligibilityGraphV2) -> GraphComparison:
    """Score an operational failure as an empty prediction in the primary denominator."""

    expected = graph_components(reference)
    return GraphComparison(
        ast_exact_match=False,
        nodes=_empty_prediction_counts(expected.nodes),
        edges=_empty_prediction_counts(expected.edges),
        predicates=_empty_prediction_counts(expected.predicates),
        concept_evidence=_empty_prediction_counts(expected.concept_evidence),
    )


def graph_components(graph: EligibilityGraphV2) -> GraphComponents:
    canonical = canonicalize_graph(graph)
    nodes: SignatureCounter = Counter()
    edges: SignatureCounter = Counter()
    predicates: SignatureCounter = Counter()
    concept_evidence: SignatureCounter = Counter()
    if canonical.root is None:
        # An explicitly non-machine-executable criterion is a real semantic
        # outcome, not an empty reference.  The sentinel makes a correct null
        # root score as one true positive and an operational failure as one
        # false negative instead of producing a vacuous all-zero comparison.
        nodes[_canonical({"kind": "root_none"})] += 1
    else:
        _visit(
            canonical.root,
            nodes=nodes,
            edges=edges,
            predicates=predicates,
            concept_evidence=concept_evidence,
        )
    return GraphComponents(
        nodes=nodes,
        edges=edges,
        predicates=predicates,
        concept_evidence=concept_evidence,
    )


def _visit(
    expression: Expression,
    *,
    nodes: SignatureCounter,
    edges: SignatureCounter,
    predicates: SignatureCounter,
    concept_evidence: SignatureCounter,
) -> None:
    parent_signature = _node_signature(expression)
    nodes[parent_signature] += 1
    if isinstance(expression, Predicate):
        predicates[_canonical(_without_evidence(expression.model_dump(mode="json")))] += 1
        concept = expression.concept.model_dump(mode="json")
        for evidence in expression.evidence:
            concept_evidence[
                _canonical({"concept": concept, "evidence": evidence.model_dump(mode="json")})
            ] += 1

    for position, child in _children(expression):
        child_signature = _node_signature(child)
        edge = {
            "parent": parent_signature,
            "child": child_signature,
            "position": position,
        }
        edges[_canonical(edge)] += 1
        _visit(
            child,
            nodes=nodes,
            edges=edges,
            predicates=predicates,
            concept_evidence=concept_evidence,
        )


def _children(expression: Expression) -> Iterator[tuple[int | None, Expression]]:
    if isinstance(expression, (AllOf, AnyOf)):
        for child in expression.children:
            yield None, child
    elif isinstance(expression, AtLeast):
        for position, child in enumerate(expression.children):
            yield position, child
    elif isinstance(expression, Not):
        yield 0, expression.child


def _node_signature(expression: Expression) -> str:
    if isinstance(expression, Predicate):
        payload = _without_evidence(expression.model_dump(mode="json"))
    elif isinstance(expression, AtLeast):
        payload = {"kind": expression.kind, "minimum": expression.minimum}
    else:
        payload = {"kind": expression.kind}
    return _canonical(payload)


def _without_evidence(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {key: _without_evidence(item) for key, item in value.items() if key != "evidence"}
    if isinstance(value, list):
        return [_without_evidence(item) for item in value]
    return value


def _match_counts(prediction: SignatureCounter, reference: SignatureCounter) -> MatchCounts:
    true_positive = sum((prediction & reference).values())
    return MatchCounts(
        true_positive=true_positive,
        false_positive=sum(prediction.values()) - true_positive,
        false_negative=sum(reference.values()) - true_positive,
    )


def _empty_prediction_counts(reference: SignatureCounter) -> MatchCounts:
    return MatchCounts(0, 0, sum(reference.values()))


def _canonical(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _ratio(numerator: int, denominator: int) -> float:
    return numerator / denominator if denominator else 0.0
