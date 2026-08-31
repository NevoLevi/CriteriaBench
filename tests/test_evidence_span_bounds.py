from __future__ import annotations

import pytest

from criteriabench.domain.schemas import EvidenceSpan, TrialDocument
from criteriabench.providers.base import ProviderResult, TokenUsage
from criteriabench.services.extraction import ProvenanceError, _validate_provenance
from tests.helpers import criterion, extraction


def test_evidence_end_offset_cannot_exceed_source_document() -> None:
    trial = TrialDocument(
        trial_id="TEST-EVIDENCE-001",
        title="Evidence bound",
        eligibility_text="Adult",
    )
    item = criterion(text="Adult").model_copy(
        update={
            "evidence": EvidenceSpan(start_char=0, end_char=999, quote="Adult"),
        }
    )
    result = ProviderResult(
        extraction=extraction(item, trial_id=trial.trial_id),
        provider="mock",
        model="test",
        latency_ms=0.0,
        usage=TokenUsage(input_tokens=0, output_tokens=0),
        estimated_cost_usd=0.0,
    )

    with pytest.raises(ProvenanceError, match="exceed"):
        _validate_provenance(trial, result)
