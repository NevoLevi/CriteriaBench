"""Keep the guarded cloud deploy aligned with the pinned Helm major version."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_azure_deploy_rolls_back_and_waits_for_migrations() -> None:
    script = (ROOT / "scripts/azure-apply-reviewed.ps1").read_text(encoding="utf-8")

    assert '"--rollback-on-failure"' in script
    assert '"--wait-for-jobs"' in script
    assert '"--wait"' in script
    assert '"--atomic"' not in script
