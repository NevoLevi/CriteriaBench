from __future__ import annotations

from copy import deepcopy

import pytest

from criteriabench.domain.schemas import EligibilityCriterion, TrialDocument
from criteriabench.providers.mock import DeterministicMockProvider
from criteriabench.suite.analysis import classify_errors, count_exact_true_positives
from criteriabench.suite.baselines import EmptyBaseline, RulesBaseline, create_baseline
from criteriabench.suite.statistics import percentile_interval
from tests.helpers import criterion, extraction


async def test_empty_and_rules_baselines_are_typed_deterministic_adapters() -> None:
    trial = TrialDocument(
        trial_id="OFFLINE-001",
        title="Offline",
        eligibility_text="Inclusion Criteria:\n- Age at least 18 years",
        source_url=None,
    )
    empty = await EmptyBaseline().predict(trial)
    rules = await RulesBaseline().predict(trial)
    provider = await DeterministicMockProvider().extract(trial)

    assert empty.trial_id == trial.trial_id
    assert empty.inclusion_criteria == []
    assert rules == provider.extraction
    assert create_baseline("empty-v1").name == "empty-v1"
    with pytest.raises(ValueError, match="unsupported"):
        create_baseline("unknown-v1")  # type: ignore[arg-type]


def test_exact_true_positives_use_duplicate_safe_multiset_intersection() -> None:
    predicted = extraction(
        criterion(criterion_id="I001", text="Age at least 18 years"),
        criterion(criterion_id="I002", text="Age at least 18 years"),
    )
    reference = extraction(criterion(text="Age at least 18 years"))
    assert count_exact_true_positives(predicted, reference) == 1


def test_taxonomy_uses_evaluator_alignment_and_granular_fields() -> None:
    reference_payload = criterion(text="Age at least 18 years").model_dump(mode="json")
    reference_payload["temporal_constraint"] = {
        "relation": "within_previous",
        "quantity": 6,
        "unit": "months",
        "reference_event": None,
        "raw_text": "within the previous 6 months",
    }
    predicted_payload = deepcopy(reference_payload)
    predicted_payload.update(
        {
            "category": "demographic",
            "concept": "adult participant",
            "value": 21,
            "unit": "year",
            "negated": True,
            "source_text": "Age at least 21 years",
            "normalized_text": "age at least 21 years",
            "evidence": {
                "start_char": 4,
                "end_char": 25,
                "quote": "Age at least 21 years",
            },
            "temporal_constraint": {
                "relation": "within_previous",
                "quantity": 3,
                "unit": "weeks",
                "reference_event": None,
                "raw_text": "within the previous 3 weeks",
            },
        }
    )
    reference = extraction(EligibilityCriterion.model_validate(reference_payload))
    predicted = extraction(EligibilityCriterion.model_validate(predicted_payload))
    errors = classify_errors(predicted, reference)

    assert errors.missing_criterion == 0
    assert errors.spurious_criterion == 0
    assert errors.text_mismatch == 1
    assert errors.category_mismatch == 1
    assert errors.concept_mismatch == 1
    assert errors.value_mismatch == 1
    assert errors.unit_mismatch == 1
    assert errors.negation_mismatch == 1
    assert errors.temporal_quantity_mismatch == 1
    assert errors.temporal_unit_mismatch == 1
    assert errors.temporal_raw_text_mismatch == 1
    assert errors.evidence_quote_mismatch == 1
    assert errors.evidence_offset_mismatch == 1


def test_taxonomy_distinguishes_logic_connector_and_parent() -> None:
    reference = extraction(
        criterion(criterion_id="I001", text="alpha beta", group_id="IG001", logic_connector="and"),
        criterion(criterion_id="I002", text="beta gamma", group_id="IG001", logic_connector="and"),
        criterion(
            criterion_id="I003",
            text="gamma delta",
            group_id="IG002",
            parent_group_id="IG001",
        ),
    )
    predicted = extraction(
        criterion(criterion_id="I001", text="alpha beta", group_id="IG001", logic_connector="or"),
        criterion(criterion_id="I002", text="beta gamma", group_id="IG001", logic_connector="or"),
        criterion(criterion_id="I003", text="gamma delta", group_id="IG002"),
    )
    errors = classify_errors(predicted, reference)
    assert errors.logic_connector_mismatch == 2
    assert errors.logic_parent_mismatch == 1


def test_percentile_bootstrap_is_seeded_and_six_decimal() -> None:
    first = percentile_interval([0.0, 0.25, 0.5, 1.0])
    second = percentile_interval([0.0, 0.25, 0.5, 1.0])
    assert first == second
    assert first.estimate == 0.4375
    assert first.resamples == 10_000
    assert first.seed == 20_260_901
