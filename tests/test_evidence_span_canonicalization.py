from __future__ import annotations

import pytest

from criteriabench.db.repositories import RunRepository
from criteriabench.db.session import Database
from criteriabench.domain.schemas import (
    ClinicalTrialEligibility,
    CriterionKind,
    TrialDocument,
)
from criteriabench.providers.base import ExtractionProvider, ProviderResult, TokenUsage
from criteriabench.services.extraction import (
    ExtractionService,
    LiveBudget,
    ProvenanceError,
    _canonicalize_provider_output,
    _validate_provenance,
)
from tests.helpers import criterion, extraction


def _result(extraction_value: ClinicalTrialEligibility) -> ProviderResult:
    return ProviderResult(
        extraction=extraction_value,
        provider="openai",
        model="gpt-5.6-luna",
        latency_ms=1.0,
        usage=TokenUsage(input_tokens=100, output_tokens=50),
        estimated_cost_usd=0.001,
    )


def _provider_result(
    trial: TrialDocument,
    *,
    start: int,
    text: str,
    trial_id: str | None = None,
    criterion_id: str = "I001",
) -> ProviderResult:
    item = criterion(
        criterion_id=criterion_id,
        text=text,
        start=start,
        operator="unspecified",
        value=None,
        unit=None,
    )
    return _result(extraction(item, trial_id=trial_id or trial.trial_id))


def test_unique_exact_quote_repairs_utf8_byte_style_offset() -> None:
    trial = TrialDocument(
        trial_id="SPAN-001",
        title="Unicode offset",
        eligibility_text="\U0001f642\nAge >= 18 years",
    )
    result = _provider_result(trial, start=5, text="Age >= 18 years")

    repaired = _canonicalize_provider_output(trial, result)

    evidence = repaired.extraction.inclusion_criteria[0].evidence
    assert evidence.start_char == 2
    assert evidence.end_char == 17
    assert trial.eligibility_text[evidence.start_char : evidence.end_char] == evidence.quote
    _validate_provenance(trial, repaired)


def test_valid_span_is_preserved_even_when_quote_occurs_more_than_once() -> None:
    trial = TrialDocument(
        trial_id="SPAN-002",
        title="Repeated quote",
        eligibility_text="Adult\nAdult",
    )
    result = _provider_result(trial, start=6, text="Adult")

    assert _canonicalize_provider_output(trial, result) is result


def test_wrong_span_with_repeated_quote_fails_closed_with_safe_code() -> None:
    trial = TrialDocument(
        trial_id="SPAN-003",
        title="Ambiguous quote",
        eligibility_text="Adult\nAdult",
    )
    result = _provider_result(trial, start=1, text="Adult")

    with pytest.raises(ProvenanceError) as raised:
        _canonicalize_provider_output(trial, result)

    assert raised.value.code == "quote_ambiguous"
    assert raised.value.safe_details == {
        "criterion_id": "I001",
        "source_length": 11,
        "quote_length": 5,
    }
    assert "Adult" not in str(raised.value)


def test_quote_absent_from_source_fails_closed_with_safe_code() -> None:
    trial = TrialDocument(
        trial_id="SPAN-004",
        title="Missing quote",
        eligibility_text="Adult",
    )
    result = _provider_result(trial, start=0, text="Minor")

    with pytest.raises(ProvenanceError) as raised:
        _canonicalize_provider_output(trial, result)

    assert raised.value.code == "quote_not_found"
    assert raised.value.safe_details == {
        "criterion_id": "I001",
        "source_length": 5,
        "quote_length": 5,
    }
    assert "Minor" not in str(raised.value)


def test_application_owns_trial_kind_and_sequential_criterion_ids() -> None:
    trial = TrialDocument(
        trial_id="REQUEST-OWNED",
        title="Application-owned fields",
        eligibility_text="Age >= 18 years\nAdult\nPregnancy",
    )
    first = criterion(criterion_id="I010", text="Age >= 18 years", start=0)
    second = criterion(
        criterion_id="I099",
        text="Adult",
        start=16,
        category="other",
        concept="adult",
        operator="unspecified",
        value=None,
        unit=None,
    )
    excluded = criterion(
        criterion_id="E099",
        kind="exclusion",
        text="Pregnancy",
        start=22,
        category="reproductive",
        concept="pregnancy",
        operator="exists",
        value=True,
        unit=None,
    )
    provider_extraction = extraction(
        first,
        second,
        excluded,
        trial_id="MODEL-OWNED",
    )
    provider_extraction = provider_extraction.model_copy(
        update={
            "inclusion_criteria": [
                first.model_copy(update={"kind": CriterionKind.EXCLUSION}),
                second,
            ],
            "exclusion_criteria": [excluded.model_copy(update={"kind": CriterionKind.INCLUSION})],
        }
    )

    canonical = _canonicalize_provider_output(trial, _result(provider_extraction)).extraction

    assert canonical.trial_id == "REQUEST-OWNED"
    assert [item.criterion_id for item in canonical.inclusion_criteria] == ["I001", "I002"]
    assert [item.kind for item in canonical.inclusion_criteria] == [
        CriterionKind.INCLUSION,
        CriterionKind.INCLUSION,
    ]
    assert [item.criterion_id for item in canonical.exclusion_criteria] == ["E001"]
    assert canonical.exclusion_criteria[0].kind is CriterionKind.EXCLUSION
    assert ClinicalTrialEligibility.model_validate(canonical.model_dump(mode="json")) == canonical


class WrongOffsetProvider(ExtractionProvider):
    name = "mock"
    model = "wrong-offset-test"

    async def extract(self, trial: TrialDocument) -> ProviderResult:
        return _provider_result(
            trial,
            start=0,
            text="Age >= 18 years",
            trial_id="MODEL-TRIAL-ID",
            criterion_id="I099",
        )


async def test_extraction_service_returns_fully_canonicalized_output() -> None:
    database = Database("sqlite+aiosqlite:///:memory:")
    trial = TrialDocument(
        trial_id="SPAN-005",
        title="Service canonicalization",
        eligibility_text="Header\nAge >= 18 years",
    )
    service = ExtractionService(
        provider=WrongOffsetProvider(),
        repository=RunRepository(database),
        live_budget=LiveBudget(0.0),
        estimated_input_tokens=100,
        max_output_tokens=100,
        input_price=0.2,
        output_price=1.2,
        max_document_characters=100_000,
    )
    try:
        outcome = await service.execute(trial, persist=False)
    finally:
        await database.close()

    extraction_value = outcome.result.extraction
    evidence = extraction_value.inclusion_criteria[0].evidence
    assert extraction_value.trial_id == trial.trial_id
    assert extraction_value.inclusion_criteria[0].criterion_id == "I001"
    assert evidence.start_char == 7
    assert evidence.end_char == 22
