"""Small builders for strict domain fixtures."""

from __future__ import annotations

from typing import Any

from criteriabench.domain.schemas import ClinicalTrialEligibility, EligibilityCriterion


def criterion(
    *,
    criterion_id: str = "I001",
    kind: str = "inclusion",
    text: str = "Age >= 18 years",
    start: int = 0,
    category: str = "age",
    concept: str = "age",
    operator: str = "greater_than_or_equal",
    value: Any = 18,
    unit: str | None = "years",
    negated: bool = False,
    temporal_relation: str = "unspecified",
    logic_connector: str = "single",
    group_id: str | None = None,
    parent_group_id: str | None = None,
) -> EligibilityCriterion:
    prefix = criterion_id[0]
    group_id = group_id or f"{prefix}G{criterion_id[1:]}"
    return EligibilityCriterion(
        criterion_id=criterion_id,
        kind=kind,
        category=category,
        source_text=text,
        normalized_text=text.casefold(),
        concept=concept,
        operator=operator,
        value=value,
        unit=unit,
        negated=negated,
        temporal_constraint={
            "relation": temporal_relation,
            "quantity": None,
            "unit": None,
            "reference_event": None,
            "raw_text": "",
        },
        logic_group={
            "group_id": group_id,
            "connector": logic_connector,
            "parent_group_id": parent_group_id,
        },
        evidence={"start_char": start, "end_char": start + len(text), "quote": text},
    )


def extraction(
    *criteria: EligibilityCriterion,
    trial_id: str = "TEST-001",
) -> ClinicalTrialEligibility:
    return ClinicalTrialEligibility(
        schema_version="1.0",
        trial_id=trial_id,
        inclusion_criteria=[item for item in criteria if item.kind.value == "inclusion"],
        exclusion_criteria=[item for item in criteria if item.kind.value == "exclusion"],
        ambiguities=[],
    )
