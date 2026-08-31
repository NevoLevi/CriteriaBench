from __future__ import annotations

import argparse
import json

import pytest

from criteriabench.benchmark_cli import run, validate_mode


def _args(**overrides: object) -> argparse.Namespace:
    values = {
        "live": False,
        "acknowledge_paid_api": False,
        "budget_usd": None,
    }
    values.update(overrides)
    return argparse.Namespace(**values)


def test_default_mode_forces_zero_cost_mock(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    monkeypatch.setenv("ALLOW_PAID_CALLS", "true")
    monkeypatch.setenv("OPENAI_API_KEY", "unit-test-placeholder")
    settings, budget = validate_mode(_args())
    assert settings.provider == "mock"
    assert settings.allow_paid_calls is False
    assert budget == 0.0


def test_live_mode_requires_two_independent_acknowledgements(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    monkeypatch.setenv("ALLOW_PAID_CALLS", "true")
    monkeypatch.setenv("OPENAI_API_KEY", "unit-test-placeholder")
    with pytest.raises(ValueError, match="acknowledge"):
        validate_mode(_args(live=True))


def test_live_mode_rejects_budget_above_absolute_ceiling(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    monkeypatch.setenv("ALLOW_PAID_CALLS", "true")
    monkeypatch.setenv("OPENAI_API_KEY", "unit-test-placeholder")
    monkeypatch.setenv("LIVE_RUN_BUDGET_USD", "3")
    with pytest.raises(ValueError, match=r"\$2"):
        validate_mode(_args(live=True, acknowledge_paid_api=True, budget_usd=3.0))


async def test_mock_benchmark_writes_no_secret_fields(tmp_path) -> None:
    trial_path = tmp_path / "trial.json"
    trial_path.write_text(
        json.dumps(
            {
                "trial_id": "TEST-001",
                "title": "Test",
                "eligibility_text": "Inclusion Criteria:\n- Adult",
                "source_url": None,
            }
        ),
        encoding="utf-8",
    )
    settings, budget = validate_mode(_args())
    artifact = await run([trial_path], settings=settings, budget_usd=budget)
    serialized = json.dumps(artifact)
    assert artifact["provider"] == "mock"
    assert artifact["total_usage_priced_cost_usd"] == 0.0
    assert len(artifact["extraction_contract_sha256"]) == 64
    assert len(artifact["evaluation_contract_sha256"]) == 64
    assert artifact["results"][0]["usage_priced_cost_usd"] == 0.0
    assert "api_key" not in serialized.casefold()
