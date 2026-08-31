from __future__ import annotations

import pytest
from pydantic import ValidationError

from criteriabench.domain.schemas import ClinicalTrialEligibility, EligibilityCriterion
from tests.helpers import criterion, extraction


def test_unknown_or_uncalibrated_confidence_field_is_rejected() -> None:
    payload = criterion().model_dump(mode="json")
    payload["confidence"] = 0.99
    with pytest.raises(ValidationError):
        EligibilityCriterion.model_validate(payload)


def test_group_prefix_must_match_criterion_kind() -> None:
    with pytest.raises(ValidationError, match="group"):
        criterion(group_id="EG001")


def test_parent_group_must_exist() -> None:
    child = criterion(group_id="IG002", parent_group_id="IG999")
    with pytest.raises(ValidationError, match="parent"):
        extraction(child)


def test_logic_group_cannot_form_a_cycle() -> None:
    first = criterion(criterion_id="I001", group_id="IG001", parent_group_id="IG002")
    second = criterion(criterion_id="I002", group_id="IG002", parent_group_id="IG001")
    with pytest.raises(ValidationError, match="cycle"):
        extraction(first, second)


def test_compound_criteria_can_share_one_or_group() -> None:
    first = criterion(criterion_id="I001", group_id="IG001", logic_connector="or")
    second = criterion(
        criterion_id="I002",
        text="Age <= 70 years",
        group_id="IG001",
        logic_connector="or",
    )
    result = extraction(first, second)
    assert result.inclusion_criteria[0].logic_group == result.inclusion_criteria[1].logic_group


def test_evidence_span_rejects_reverse_offsets() -> None:
    payload = criterion().model_dump(mode="json")
    payload["evidence"] = {"start_char": 5, "end_char": 4, "quote": "x"}
    with pytest.raises(ValidationError):
        ClinicalTrialEligibility(
            schema_version="1.0",
            trial_id="TEST-001",
            inclusion_criteria=[payload],
            exclusion_criteria=[],
            ambiguities=[],
        )
