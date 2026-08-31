from __future__ import annotations

from criteriabench.evaluation.metrics import evaluate_extraction
from tests.helpers import extraction


def test_two_empty_valid_extractions_are_a_perfect_match() -> None:
    predicted = extraction()
    reference = extraction()
    report = evaluate_extraction(predicted, reference)
    assert report.exact_match_precision == 1.0
    assert report.exact_match_recall == 1.0
    assert report.exact_match_f1 == 1.0
    assert report.token_f1 == 1.0
    assert report.macro_field_accuracy == 1.0
