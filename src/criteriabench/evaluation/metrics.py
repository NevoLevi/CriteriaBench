"""Deterministic extraction-quality metrics suitable for CI regression gates."""

from __future__ import annotations

import json
import re
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from pydantic import Field

from criteriabench.domain.schemas import (
    ClinicalTrialEligibility,
    EligibilityCriterion,
    StrictModel,
)

_TOKEN = re.compile(r"[\w]+", re.UNICODE)
MIN_ALIGNMENT_TOKEN_F1 = 0.25
_FLOAT_EPSILON = 1e-12


class EvaluationReport(StrictModel):
    """Quality measurements for one prediction/reference pair."""

    schema_valid: bool
    exact_match_precision: float = Field(ge=0.0, le=1.0)
    exact_match_recall: float = Field(ge=0.0, le=1.0)
    exact_match_f1: float = Field(ge=0.0, le=1.0)
    token_f1: float = Field(ge=0.0, le=1.0)
    category_accuracy: float = Field(ge=0.0, le=1.0)
    concept_accuracy: float = Field(ge=0.0, le=1.0)
    operator_accuracy: float = Field(ge=0.0, le=1.0)
    value_accuracy: float = Field(ge=0.0, le=1.0)
    unit_accuracy: float = Field(ge=0.0, le=1.0)
    negated_accuracy: float = Field(ge=0.0, le=1.0)
    temporal_relation_accuracy: float = Field(ge=0.0, le=1.0)
    logic_connector_accuracy: float = Field(ge=0.0, le=1.0)
    macro_field_accuracy: float = Field(ge=0.0, le=1.0)
    predicted_count: int = Field(ge=0)
    reference_count: int = Field(ge=0)


@dataclass(frozen=True, slots=True)
class _Pair:
    predicted: EligibilityCriterion
    reference: EligibilityCriterion
    token_score: float


def evaluate_extraction(
    predicted: ClinicalTrialEligibility,
    reference: ClinicalTrialEligibility,
) -> EvaluationReport:
    """Evaluate normalized criteria with duplicate-safe, one-to-one alignment."""

    predicted_items = predicted.inclusion_criteria + predicted.exclusion_criteria
    reference_items = reference.inclusion_criteria + reference.exclusion_criteria
    predicted_keys = Counter(_criterion_key(item) for item in predicted_items)
    reference_keys = Counter(_criterion_key(item) for item in reference_items)

    true_positives = sum((predicted_keys & reference_keys).values())
    both_empty = not predicted_items and not reference_items
    precision = _safe_ratio(
        true_positives,
        sum(predicted_keys.values()),
        empty_value=1.0 if both_empty else 0.0,
    )
    recall = _safe_ratio(
        true_positives,
        sum(reference_keys.values()),
        empty_value=1.0 if both_empty else 0.0,
    )
    exact_f1 = _harmonic_mean(precision, recall)

    pairs = _align(predicted_items, reference_items)
    denominator = max(len(predicted_items), len(reference_items))
    empty_value = 1.0 if denominator == 0 else 0.0
    token_f1 = _safe_ratio(
        sum(pair.token_score for pair in pairs),
        denominator,
        empty_value=empty_value,
    )

    field_extractors: dict[str, Callable[[EligibilityCriterion], Any]] = {
        "category_accuracy": lambda item: item.category.value,
        "concept_accuracy": lambda item: _normalize(item.concept),
        "operator_accuracy": lambda item: item.operator.value,
        "value_accuracy": lambda item: _stable_value(item.value),
        "unit_accuracy": lambda item: _normalize(item.unit or ""),
        "negated_accuracy": lambda item: item.negated,
        "temporal_relation_accuracy": (lambda item: item.temporal_constraint.relation.value),
        "logic_connector_accuracy": lambda item: item.logic_group.connector.value,
    }
    field_scores = {
        name: _field_accuracy(pairs, denominator, extractor, empty_value)
        for name, extractor in field_extractors.items()
    }
    macro_field_accuracy = sum(field_scores.values()) / len(field_scores)

    return EvaluationReport(
        # Both arguments already crossed Pydantic's typed boundary. This is not
        # a claim of semantic or clinical correctness.
        schema_valid=True,
        exact_match_precision=round(precision, 6),
        exact_match_recall=round(recall, 6),
        exact_match_f1=round(exact_f1, 6),
        token_f1=round(token_f1, 6),
        macro_field_accuracy=round(macro_field_accuracy, 6),
        predicted_count=len(predicted_items),
        reference_count=len(reference_items),
        **{name: round(score, 6) for name, score in field_scores.items()},
    )


def _criterion_key(item: EligibilityCriterion) -> tuple[str, str]:
    return item.kind.value, _normalize(item.normalized_text)


def _align(
    predicted: list[EligibilityCriterion],
    reference: list[EligibilityCriterion],
) -> list[_Pair]:
    """Return deterministic maximum-weight same-kind matches above a frozen floor."""

    if not predicted or not reference:
        return []
    weights: list[list[float]] = []
    for predicted_item in predicted:
        row: list[float] = []
        for reference_item in reference:
            if predicted_item.kind is not reference_item.kind:
                row.append(0.0)
                continue
            score = _token_f1(
                predicted_item.normalized_text,
                reference_item.normalized_text,
            )
            row.append(score if score >= MIN_ALIGNMENT_TOKEN_F1 else 0.0)
        weights.append(row)

    pairs: list[_Pair] = []
    for predicted_index, reference_index in _maximum_weight_assignment(weights):
        score = weights[predicted_index][reference_index]
        if score < MIN_ALIGNMENT_TOKEN_F1:
            continue
        pairs.append(
            _Pair(
                predicted=predicted[predicted_index],
                reference=reference[reference_index],
                token_score=score,
            )
        )
    return pairs


def _maximum_weight_assignment(weights: list[list[float]]) -> list[tuple[int, int]]:
    """Solve a rectangular assignment with a deterministic Hungarian algorithm."""

    row_count = len(weights)
    column_count = len(weights[0]) if weights else 0
    if row_count == 0 or column_count == 0:
        return []
    size = max(row_count, column_count)
    maximum = max(max(row) for row in weights)
    costs = [
        [
            maximum
            - (
                weights[row_index][column_index]
                if row_index < row_count and column_index < column_count
                else 0.0
            )
            for column_index in range(size)
        ]
        for row_index in range(size)
    ]

    row_potential = [0.0] * (size + 1)
    column_potential = [0.0] * (size + 1)
    column_match = [0] * (size + 1)
    previous_column = [0] * (size + 1)
    for row in range(1, size + 1):
        column_match[0] = row
        minimum = [math_inf()] * (size + 1)
        used = [False] * (size + 1)
        column = 0
        while True:
            used[column] = True
            matched_row = column_match[column]
            delta = math_inf()
            next_column = 0
            for candidate_column in range(1, size + 1):
                if used[candidate_column]:
                    continue
                reduced_cost = (
                    costs[matched_row - 1][candidate_column - 1]
                    - row_potential[matched_row]
                    - column_potential[candidate_column]
                )
                if reduced_cost < minimum[candidate_column] - _FLOAT_EPSILON:
                    minimum[candidate_column] = reduced_cost
                    previous_column[candidate_column] = column
                if minimum[candidate_column] < delta - _FLOAT_EPSILON:
                    delta = minimum[candidate_column]
                    next_column = candidate_column
            for candidate_column in range(size + 1):
                if used[candidate_column]:
                    row_potential[column_match[candidate_column]] += delta
                    column_potential[candidate_column] -= delta
                else:
                    minimum[candidate_column] -= delta
            column = next_column
            if column_match[column] == 0:
                break
        while True:
            previous = previous_column[column]
            column_match[column] = column_match[previous]
            column = previous
            if column == 0:
                break

    assignment: list[tuple[int, int]] = []
    for column in range(1, size + 1):
        row = column_match[column]
        if 1 <= row <= row_count and column <= column_count:
            assignment.append((row - 1, column - 1))
    return sorted(assignment)


def math_inf() -> float:
    """Keep the assignment implementation import-free and easy to unit test."""

    return float("inf")


def _field_accuracy(
    pairs: list[_Pair],
    denominator: int,
    extractor: Callable[[EligibilityCriterion], Any],
    empty_value: float,
) -> float:
    matches = sum(extractor(pair.predicted) == extractor(pair.reference) for pair in pairs)
    return _safe_ratio(matches, denominator, empty_value=empty_value)


def _stable_value(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _normalize(text: str) -> str:
    return " ".join(_TOKEN.findall(text.casefold()))


def _token_f1(left: str, right: str) -> float:
    left_tokens = Counter(_TOKEN.findall(left.casefold()))
    right_tokens = Counter(_TOKEN.findall(right.casefold()))
    overlap = sum((left_tokens & right_tokens).values())
    if not left_tokens and not right_tokens:
        return 1.0
    precision = _safe_ratio(overlap, sum(left_tokens.values()))
    recall = _safe_ratio(overlap, sum(right_tokens.values()))
    return _harmonic_mean(precision, recall)


def _safe_ratio(
    numerator: int | float,
    denominator: int,
    *,
    empty_value: float = 0.0,
) -> float:
    return numerator / denominator if denominator else empty_value


def _harmonic_mean(precision: float, recall: float) -> float:
    return 2 * precision * recall / (precision + recall) if precision + recall else 0.0
