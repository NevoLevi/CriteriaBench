from __future__ import annotations

import pytest

from criteriabench.evaluation.metrics import evaluate_extraction
from tests.helpers import criterion, extraction


def test_exact_matching_counts_duplicates_as_a_multiset() -> None:
    predicted = extraction(
        criterion(criterion_id="I001"),
        criterion(criterion_id="I002"),
    )
    reference = extraction(criterion(criterion_id="I001"))
    report = evaluate_extraction(predicted, reference)
    assert report.exact_match_precision == 0.5
    assert report.exact_match_recall == 1.0
    assert report.exact_match_f1 == pytest.approx(2 / 3, abs=1e-6)


def test_one_prediction_cannot_match_two_references() -> None:
    predicted = extraction(criterion(criterion_id="I001"))
    reference = extraction(
        criterion(criterion_id="I001"),
        criterion(criterion_id="I002"),
    )
    report = evaluate_extraction(predicted, reference)
    assert report.token_f1 == 0.5


def test_wrong_structured_field_lowers_only_relevant_and_macro_scores() -> None:
    predicted = extraction(criterion(category="demographic"))
    reference = extraction(criterion(category="age"))
    report = evaluate_extraction(predicted, reference)
    assert report.exact_match_f1 == 1.0
    assert report.category_accuracy == 0.0
    assert report.concept_accuracy == 1.0
    assert report.operator_accuracy == 1.0
    assert report.value_accuracy == 1.0
    assert report.macro_field_accuracy < 1.0


def test_negation_is_scored_explicitly() -> None:
    predicted = extraction(criterion(negated=False))
    reference = extraction(criterion(negated=True))
    report = evaluate_extraction(predicted, reference)
    assert report.negated_accuracy == 0.0
    assert report.category_accuracy == 1.0
