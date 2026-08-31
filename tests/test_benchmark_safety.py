from __future__ import annotations

import argparse
import math
from pathlib import Path

import pytest

from criteriabench.benchmark_cli import (
    PROJECT_ROOT,
    _parser,
    _validate_live_output,
    run,
    validate_mode,
    validate_paths,
)


def _live_args(budget: float) -> argparse.Namespace:
    return argparse.Namespace(
        live=True,
        acknowledge_paid_api=True,
        budget_usd=budget,
    )


def test_nan_cannot_bypass_live_budget_checks(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    monkeypatch.setenv("ALLOW_PAID_CALLS", "true")
    monkeypatch.setenv("OPENAI_API_KEY", "unit-test-placeholder")
    with pytest.raises(ValueError, match="finite"):
        validate_mode(_live_args(math.nan))


def test_output_cannot_overwrite_input_or_use_env_like_files(tmp_path: Path) -> None:
    input_path = tmp_path / "case.json"
    with pytest.raises(ValueError, match=r"output|overwrite"):
        validate_paths([input_path], input_path)
    with pytest.raises(ValueError, match="env"):
        validate_paths([tmp_path / ".env.local"], tmp_path / "artifact.json")


async def test_synthetic_gold_fixture_produces_scored_reproducible_artifact() -> None:
    settings, budget = validate_mode(
        argparse.Namespace(live=False, acknowledge_paid_api=False, budget_usd=None)
    )
    artifact = await run(
        [Path("data/synthetic/benchmark_case_001.json")],
        settings=settings,
        budget_usd=budget,
    )
    result = artifact["results"][0]
    assert len(result["fixture_sha256"]) == 64
    int(result["fixture_sha256"], 16)
    assert result["evaluation"]["schema_valid"] is True
    assert result["usage_priced_cost_usd"] == 0.0
    assert artifact["evaluated_cases"] == 1
    assert artifact["status"] == "completed"
    assert artifact["paid"] is False
    assert artifact["mean_exact_match_f1"] == 1.0
    assert artifact["mean_token_f1"] == 1.0
    assert artifact["mean_macro_field_accuracy"] == 0.875
    assert artifact["total_usage_priced_cost_usd"] == 0.0
    for hash_field in (
        "extraction_contract_sha256",
        "evaluation_contract_sha256",
    ):
        assert len(artifact[hash_field]) == 64
        int(artifact[hash_field], 16)


def test_existing_output_requires_explicit_overwrite(tmp_path: Path) -> None:
    input_path = tmp_path / "case.json"
    output_path = tmp_path / "artifact.json"
    input_path.write_text("{}", encoding="utf-8")
    output_path.write_text("existing", encoding="utf-8")
    with pytest.raises(ValueError, match="overwrite"):
        validate_paths([input_path], output_path)
    validate_paths([input_path], output_path, allow_overwrite=True)


def test_live_output_is_confined_to_project_artifacts(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="artifacts"):
        _validate_live_output(tmp_path / "live.json")
    _validate_live_output(PROJECT_ROOT / "artifacts" / "live.json")


def test_help_exposes_budget_acknowledgement_and_overwrite_controls() -> None:
    help_text = _parser().format_help()
    assert "--budget-usd" in help_text
    assert "--acknowledge-paid-api" in help_text
    assert "--overwrite" in help_text
