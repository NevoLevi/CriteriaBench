"""Regression checks for disposable PostgreSQL under restricted pod security."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _read(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def test_raw_kind_postgres_uses_a_nonroot_writable_pgdata_child() -> None:
    manifest = _read("deploy/k8s/overlays/kind/demo-dependencies.yaml")

    assert "runAsUser: 70" in manifest
    assert "fsGroup: 70" in manifest
    assert "fsGroupChangePolicy: OnRootMismatch" in manifest
    assert "value: /var/lib/postgresql/data/pgdata" in manifest
    assert "initContainers:" not in manifest


def test_helm_demo_postgres_uses_the_same_nonroot_pattern() -> None:
    template = _read("deploy/helm/criteriabench/templates/demo-dependencies.yaml")

    assert "runAsUser: 70" in template
    assert "fsGroup: 70" in template
    assert "fsGroupChangePolicy: OnRootMismatch" in template
    assert "value: /var/lib/postgresql/data/pgdata" in template
    assert "initContainers:" not in template
