"""Bound the post-rollout loopback health retry used by kind-up."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_kind_up_retries_transient_loopback_health_failures() -> None:
    script = (ROOT / "scripts/kind-up.ps1").read_text(encoding="utf-8")

    assert "function Wait-CriteriaBenchLoopbackHealth" in script
    assert "for ($attempt = 1; $attempt -le $Attempts; $attempt++)" in script
    assert 'Uri "http://127.0.0.1:8080/healthz"' in script
    assert "Start-Sleep -Milliseconds $DelayMilliseconds" in script
    assert "Wait-CriteriaBenchLoopbackHealth" in script
    assert "did not pass its loopback health check" in script
