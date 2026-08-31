from __future__ import annotations

import pytest
from pydantic import ValidationError

from tests.helpers import criterion, extraction


@pytest.mark.parametrize(
    ("operator", "value"),
    [
        ("unspecified", 18),
        ("greater_than", True),
        ("between", ["only-one-bound"]),
        ("in", []),
        ("exists", "yes"),
    ],
)
def test_operator_and_value_must_be_semantically_compatible(
    operator: str,
    value: object,
) -> None:
    with pytest.raises(ValidationError, match=r"operator|value"):
        criterion(operator=operator, value=value)


def test_unspecified_time_cannot_carry_a_duration() -> None:
    payload = criterion().model_dump(mode="json")
    payload["temporal_constraint"] = {
        "relation": "unspecified",
        "quantity": 30,
        "unit": "days",
        "reference_event": None,
        "raw_text": "within 30 days",
    }
    with pytest.raises(ValidationError, match=r"temporal|unspecified"):
        type(criterion()).model_validate(payload)


def test_single_logic_group_has_exactly_one_member() -> None:
    first = criterion(criterion_id="I001", group_id="IG001", logic_connector="single")
    second = criterion(criterion_id="I002", group_id="IG001", logic_connector="single")
    with pytest.raises(ValidationError, match="single"):
        extraction(first, second)


def test_and_or_logic_group_requires_multiple_members() -> None:
    only = criterion(group_id="IG001", logic_connector="or")
    with pytest.raises(ValidationError, match=r"multiple|two|or"):
        extraction(only)


def test_source_text_must_equal_evidence_quote() -> None:
    payload = criterion().model_dump(mode="json")
    payload["evidence"]["quote"] = "different text"
    with pytest.raises(ValidationError, match=r"source_text|evidence"):
        type(criterion()).model_validate(payload)
