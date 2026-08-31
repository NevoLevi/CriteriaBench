from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from criteriabench.domain.schemas import TrialDocument
from criteriabench.providers.openai import OpenAIResponsesProvider
from tests.helpers import criterion, extraction


class FakeResponses:
    def __init__(self, output_text: str) -> None:
        self.output_text = output_text
        self.kwargs: dict[str, Any] | None = None

    async def create(self, **kwargs: Any) -> Any:
        self.kwargs = kwargs
        return SimpleNamespace(
            id="resp_test",
            output_text=self.output_text,
            usage=SimpleNamespace(input_tokens=3_000, output_tokens=800),
        )


async def test_responses_adapter_requests_strict_schema_and_records_cost() -> None:
    expected = extraction(criterion()).model_dump_json()
    responses = FakeResponses(expected)
    provider = OpenAIResponsesProvider(
        api_key="unit-test-placeholder",
        model="gpt-5.6-luna",
        timeout_seconds=1,
        max_retries=0,
        max_output_tokens=8_000,
        input_cost_per_million_usd=0.20,
        output_cost_per_million_usd=1.20,
        client=SimpleNamespace(responses=responses),
    )
    trial = TrialDocument(
        trial_id="TEST-001",
        title="Test",
        eligibility_text="Age >= 18 years",
    )
    result = await provider.extract(trial)
    assert result.extraction == extraction(criterion())
    assert result.estimated_cost_usd == pytest.approx(0.00156)
    assert responses.kwargs is not None
    assert responses.kwargs["store"] is False
    assert responses.kwargs["reasoning"] == {"effort": "none"}
    assert responses.kwargs["text"]["format"]["strict"] is True
    assert responses.kwargs["text"]["format"]["type"] == "json_schema"


async def test_manual_price_configuration_is_applied_to_recorded_cost() -> None:
    responses = FakeResponses(extraction(criterion()).model_dump_json())
    provider = OpenAIResponsesProvider(
        api_key="unit-test-placeholder",
        model="custom-model-alias",
        timeout_seconds=1,
        max_retries=0,
        max_output_tokens=1_000,
        input_cost_per_million_usd=2.0,
        output_cost_per_million_usd=10.0,
        client=SimpleNamespace(responses=responses),
    )
    result = await provider.extract(
        TrialDocument(
            trial_id="TEST-001",
            title="Test",
            eligibility_text="Age >= 18 years",
        )
    )
    assert result.estimated_cost_usd == pytest.approx(0.014)
