"""Prevent the unauthenticated local demo API from binding beyond loopback."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_kind_host_mapping_is_loopback_only() -> None:
    cluster = (ROOT / "deploy/kind/cluster.yaml").read_text(encoding="utf-8")

    assert "containerPort: 30080" in cluster
    assert "hostPort: 8080" in cluster
    assert 'listenAddress: "127.0.0.1"' in cluster
    assert "containerPort: 30443" not in cluster
    assert "hostPort: 8443" not in cluster
