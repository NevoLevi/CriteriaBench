from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

import criteriabench.providers.openai as openai_module
from criteriabench.providers.openai import OPENAI_API_BASE_URL, OpenAIResponsesProvider


def test_provider_ignores_hostile_ambient_base_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    def fake_client(**kwargs: Any) -> Any:
        captured.update(kwargs)
        return SimpleNamespace(responses=SimpleNamespace())

    monkeypatch.setenv("OPENAI_BASE_URL", "https://attacker.invalid/v1")
    monkeypatch.setattr(openai_module, "AsyncOpenAI", fake_client)

    OpenAIResponsesProvider(
        api_key="unit-test-placeholder",
        model="gpt-5.6-luna",
        timeout_seconds=1,
        max_retries=0,
        max_output_tokens=1_000,
        input_cost_per_million_usd=0.20,
        output_cost_per_million_usd=1.20,
    )

    assert OPENAI_API_BASE_URL == "https://api.openai.com/v1"
    assert captured["base_url"] == OPENAI_API_BASE_URL
    assert captured["base_url"] != "https://attacker.invalid/v1"
