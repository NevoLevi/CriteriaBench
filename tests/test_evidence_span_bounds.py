from __future__ import annotations

import pytest

from criteriabench.domain.schemas import EvidenceSpan, TrialDocument
from criteriabench.providers.base import ProviderResult, TokenUsage
from criteriabench.services.extraction import ProvenanceError, _validate_provenance
from tests.helpers import criterion, extraction


def _result(*, trial_id: str, item: object) -> ProviderResult:
    return ProviderResult(
        extraction=extraction(item, trial_id=trial_id),  # type: ignore[arg-type]
        provider="mock",
        model="test",
        latency_ms=0.0,
        usage=TokenUsage(input_tokens=0, output_tokens=0),
        estimated_cost_usd=0.0,
    )


def test_evidence_end_offset_cannot_exceed_source_document() -> None:
    trial = TrialDocument(
        trial_id="TEST-EVIDENCE-001",
        title="Evidence bound",
        eligibility_text="Adult",
    )
    item = criterion(
        text="Adult",
        operator="unspecified",
        value=None,
        unit=None,
    ).model_copy(
        update={
            "evidence": EvidenceSpan(start_char=0, end_char=999, quote="Adult"),
        }
    )

    with pytest.raises(ProvenanceError) as raised:
        _validate_provenance(trial, _result(trial_id=trial.trial_id, item=item))

    assert raised.value.code == "out_of_bounds"
    assert raised.value.safe_details == {
        "criterion_id": "I001",
        "source_length": 5,
        "quote_length": 5,
    }
    assert "Adult" not in str(raised.value)


def test_trial_id_mismatch_has_metadata_only_diagnostic() -> None:
    trial = TrialDocument(
        trial_id="REQUEST-ID",
        title="Trial identity",
        eligibility_text="Adult",
    )
    item = criterion(
        text="Adult",
        operator="unspecified",
        value=None,
        unit=None,
    )

    with pytest.raises(ProvenanceError) as raised:
        _validate_provenance(trial, _result(trial_id="MODEL-ID", item=item))

    assert raised.value.code == "trial_id_mismatch"
    assert raised.value.safe_details == {"source_length": 5}
    assert "REQUEST-ID" not in str(raised.value)
    assert "MODEL-ID" not in str(raised.value)


def test_quote_offset_mismatch_has_metadata_only_diagnostic() -> None:
    trial = TrialDocument(
        trial_id="TEST-EVIDENCE-002",
        title="Evidence mismatch",
        eligibility_text="Adult",
    )
    item = criterion(
        text="Adult",
        operator="unspecified",
        value=None,
        unit=None,
    ).model_copy(
        update={
            "evidence": EvidenceSpan(start_char=0, end_char=4, quote="Adult"),
        }
    )

    with pytest.raises(ProvenanceError) as raised:
        _validate_provenance(trial, _result(trial_id=trial.trial_id, item=item))

    assert raised.value.code == "span_mismatch"
    assert raised.value.safe_details == {
        "criterion_id": "I001",
        "source_length": 5,
        "quote_length": 5,
    }
    assert "Adult" not in str(raised.value)
