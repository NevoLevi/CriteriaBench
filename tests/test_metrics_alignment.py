from __future__ import annotations

from criteriabench.evaluation.metrics import evaluate_extraction
from tests.helpers import criterion, extraction


def test_zero_overlap_items_do_not_receive_coincidental_field_credit() -> None:
    predicted = extraction(criterion(text="Age >= 18 years", category="age"))
    reference = extraction(
        criterion(
            text="Histologically confirmed pancreatic adenocarcinoma",
            category="age",
            concept="age",
            operator="greater_than_or_equal",
            value=18,
            unit="years",
        )
    )
    report = evaluate_extraction(predicted, reference)
    assert report.token_f1 == 0.0
    assert report.category_accuracy == 0.0
    assert report.operator_accuracy == 0.0
