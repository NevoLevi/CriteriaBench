from __future__ import annotations

import argparse
from pathlib import Path

import pytest

from criteriabench.benchmark_cli import (
    _verify_manifested_live_inputs,
    run,
    validate_mode,
)


async def test_live_boundary_accepts_only_hash_pinned_project_fixtures(tmp_path: Path) -> None:
    await _verify_manifested_live_inputs([Path("data/public/NCT04280705.json")])
    private_fixture = tmp_path / "private.json"
    private_fixture.write_text("{}", encoding="utf-8")
    with pytest.raises(ValueError, match="hash-pinned"):
        await _verify_manifested_live_inputs([private_fixture])


async def test_artifact_preserves_fixture_provenance_without_absolute_source_path() -> None:
    settings, budget = validate_mode(
        argparse.Namespace(live=False, acknowledge_paid_api=False, budget_usd=None)
    )
    artifact = await run(
        [Path("data/synthetic/benchmark_case_001.json")],
        settings=settings,
        budget_usd=budget,
    )
    case = artifact["results"][0]
    assert case["source_path"] == "data/synthetic/benchmark_case_001.json"
    assert case["provenance"]["kind"] == "synthetic"
