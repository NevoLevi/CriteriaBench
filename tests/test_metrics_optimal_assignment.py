from __future__ import annotations

from criteriabench.evaluation.metrics import evaluate_extraction
from tests.helpers import criterion, extraction


def test_alignment_uses_global_maximum_weight_not_a_greedy_pair() -> None:
    predicted = extraction(
        criterion(criterion_id="I001", text="alpha beta"),
        criterion(criterion_id="I002", text="alpha"),
    )
    reference = extraction(
        criterion(criterion_id="I001", text="alpha beta"),
        criterion(criterion_id="I002", text="beta"),
    )
    report = evaluate_extraction(predicted, reference)
    assert report.token_f1 == 0.666667
