"""Keep local evidence, databases, and test debris outside every image build context."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_docker_context_excludes_local_data_and_evidence() -> None:
    ignored = {
        line.strip()
        for line in (ROOT / ".dockerignore").read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    }

    assert {
        ".pytest_tmp*",
        "artifacts",
        "reports",
        "*.db",
        "*.sqlite",
        "*.sqlite3",
    } <= ignored
    assert "uv.lock" not in ignored
