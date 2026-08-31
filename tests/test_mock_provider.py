from __future__ import annotations

from criteriabench.domain.schemas import TrialDocument
from criteriabench.providers.mock import DeterministicMockProvider


async def test_mock_provider_is_deterministic_and_free(trial: TrialDocument) -> None:
    provider = DeterministicMockProvider()
    first = await provider.extract(trial)
    second = await provider.extract(trial)
    assert first.extraction == second.extraction
    assert first.estimated_cost_usd == 0.0
    assert first.usage.total_tokens == 0
    assert len(first.extraction.inclusion_criteria) == 2
    assert len(first.extraction.exclusion_criteria) == 1


async def test_mock_evidence_offsets_are_exact(trial: TrialDocument) -> None:
    result = await DeterministicMockProvider().extract(trial)
    for item in result.extraction.inclusion_criteria + result.extraction.exclusion_criteria:
        evidence = item.evidence
        assert trial.eligibility_text[evidence.start_char : evidence.end_char] == evidence.quote
        assert item.source_text == evidence.quote


async def test_mock_extracts_negation_time_and_logic_group() -> None:
    trial = TrialDocument(
        trial_id="TEST-NEGATION",
        title="Negation test",
        eligibility_text=(
            "Exclusion Criteria:\n- No chemotherapy within 30 days before enrollment"
        ),
    )
    result = await DeterministicMockProvider().extract(trial)
    item = result.extraction.exclusion_criteria[0]
    assert item.negated is True
    assert item.temporal_constraint.relation.value == "within_previous"
    assert item.temporal_constraint.quantity == 30
    assert item.temporal_constraint.unit.value == "days"
    assert item.logic_group.connector.value == "single"
