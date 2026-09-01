from __future__ import annotations

import json
from pathlib import Path

import pytest

import criteriabench.benchmark_cli as benchmark_cli
from criteriabench.config import Settings
from criteriabench.domain.schemas import TrialDocument
from criteriabench.providers.base import ExtractionProvider, ProviderResult, TokenUsage
from tests.helpers import criterion, extraction

FIXTURE = Path("data/synthetic/benchmark_case_001.json")
MISSING_QUOTE = "SENSITIVE-MISSING-QUOTE"


class MissingQuoteProvider(ExtractionProvider):
    name = "mock"
    model = "safe-provenance-failure-test"

    async def extract(self, trial: TrialDocument) -> ProviderResult:
        item = criterion(
            criterion_id="I099",
            text=MISSING_QUOTE,
            start=0,
            operator="unspecified",
            value=None,
            unit=None,
        )
        return ProviderResult(
            extraction=extraction(item, trial_id="MODEL-TRIAL-ID"),
            provider=self.name,
            model=self.model,
            latency_ms=1.0,
            usage=TokenUsage(input_tokens=0, output_tokens=0),
            estimated_cost_usd=0.0,
        )


async def test_benchmark_provenance_failure_contains_only_safe_diagnostics(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        benchmark_cli,
        "create_provider",
        lambda _settings: MissingQuoteProvider(),
    )
    settings = Settings(
        _env_file=None,
        LLM_PROVIDER="mock",
        ALLOW_PAID_CALLS=False,
    )

    artifact = await benchmark_cli.run([FIXTURE], settings=settings, budget_usd=0.0)

    assert artifact["status"] == "partial_failure"
    assert artifact["evaluated_cases"] == 0
    failure = artifact["results"][0]
    assert failure["error_type"] == "ProvenanceError"
    assert failure["error_code"] == "quote_not_found"
    assert failure["error_details"] == {
        "criterion_id": "I001",
        "source_length": 70,
        "quote_length": len(MISSING_QUOTE),
    }
    assert set(failure["error_details"]) == {
        "criterion_id",
        "source_length",
        "quote_length",
    }
    serialized_failure = json.dumps(failure)
    assert MISSING_QUOTE not in serialized_failure
    assert "MODEL-TRIAL-ID" not in serialized_failure
    assert "error_message" not in failure
    assert "extraction" not in failure
