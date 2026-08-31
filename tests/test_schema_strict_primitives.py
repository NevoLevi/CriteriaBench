from __future__ import annotations

import pytest
from pydantic import ValidationError

from criteriabench.domain.schemas import (
    ClinicalTrialEligibility,
    EligibilityCriterion,
    EvidenceSpan,
    TemporalConstraint,
)
from tests.helpers import criterion, extraction


@pytest.mark.parametrize(
    ("field", "value"),
    [("start_char", "0"), ("end_char", "5")],
)
def test_evidence_offsets_reject_numeric_strings(field: str, value: str) -> None:
    payload: dict[str, object] = {
        "start_char": 0,
        "end_char": 5,
        "quote": "Adult",
    }
    payload[field] = value
    with pytest.raises(ValidationError):
        EvidenceSpan.model_validate(payload)


def test_temporal_quantity_rejects_numeric_string() -> None:
    with pytest.raises(ValidationError):
        TemporalConstraint.model_validate(
            {
                "relation": "within_previous",
                "quantity": "6",
                "unit": "months",
                "reference_event": None,
                "raw_text": "within the previous 6 months",
            }
        )


def test_negated_rejects_boolean_string() -> None:
    payload = criterion().model_dump(mode="json")
    payload["negated"] = "false"
    with pytest.raises(ValidationError):
        EligibilityCriterion.model_validate(payload)


def test_strict_primitives_still_accept_valid_structured_json() -> None:
    expected = extraction(criterion())
    assert ClinicalTrialEligibility.model_validate_json(expected.model_dump_json()) == expected
