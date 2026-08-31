from __future__ import annotations

import pytest

from criteriabench.domain.schemas import TrialDocument


@pytest.fixture
def trial() -> TrialDocument:
    return TrialDocument(
        trial_id="TEST-001",
        title="Synthetic eligibility fixture",
        eligibility_text=(
            "Inclusion Criteria:\n"
            "- Age >= 18 years\n"
            "- ECOG performance status <= 1\n"
            "\nExclusion Criteria:\n"
            "- Chemotherapy within 30 days before enrollment"
        ),
        source_url=None,
    )
