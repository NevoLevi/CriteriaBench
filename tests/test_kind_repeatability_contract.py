"""Ensure a reused kind cluster cannot silently exercise stale code or schema."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_kind_up_refreshes_migration_and_constant_tag_workloads() -> None:
    script = (ROOT / "scripts/kind-up.ps1").read_text(encoding="utf-8")

    assert '"delete", "job/migrate"' in script
    assert '"--ignore-not-found=true", "--wait=true"' in script
    assert '"rollout", "restart", "deployment/api"' in script
    assert '"rollout", "restart", "deployment/worker"' in script
