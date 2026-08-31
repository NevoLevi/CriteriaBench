from __future__ import annotations

import argparse

import pytest
from pydantic import ValidationError

from criteriabench.benchmark_cli import validate_mode
from criteriabench.config import Settings


def test_paid_settings_reject_unreviewed_model_even_with_matching_alias() -> None:
    with pytest.raises(ValidationError, match="reviewed"):
        Settings(
            _env_file=None,
            LLM_PROVIDER="openai",
            ALLOW_PAID_CALLS=True,
            OPENAI_MODEL="gpt-5.6-terra",
            PRICING_MODEL="gpt-5.6-terra",
        )


def test_paid_settings_reject_rate_override_for_reviewed_model() -> None:
    with pytest.raises(ValidationError, match="rates"):
        Settings(
            _env_file=None,
            LLM_PROVIDER="openai",
            ALLOW_PAID_CALLS=True,
            INPUT_COST_PER_MILLION_USD=0.01,
        )


def test_live_cli_requires_operator_selected_run_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    monkeypatch.setenv("ALLOW_PAID_CALLS", "true")
    monkeypatch.setenv("OPENAI_API_KEY", "unit-test-placeholder")
    with pytest.raises(ValueError, match="budget-usd"):
        validate_mode(
            argparse.Namespace(
                live=True,
                acknowledge_paid_api=True,
                budget_usd=None,
            )
        )
