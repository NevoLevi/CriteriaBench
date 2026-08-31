from __future__ import annotations

import sys
from typing import Any

import pytest

import criteriabench.cli as cli


def test_local_api_cli_binds_loopback_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    def fake_run(app: str, **kwargs: Any) -> None:
        captured["app"] = app
        captured.update(kwargs)

    monkeypatch.setattr(sys, "argv", ["criteriabench-api"])
    monkeypatch.setattr(cli.uvicorn, "run", fake_run)

    cli.api()

    assert captured["host"] == "127.0.0.1"
    assert captured["port"] == 8000
    assert captured["factory"] is True


def test_local_api_cli_requires_explicit_host_for_wider_bind(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    def fake_run(app: str, **kwargs: Any) -> None:
        del app
        captured.update(kwargs)

    monkeypatch.setattr(
        sys,
        "argv",
        ["criteriabench-api", "--host", "0.0.0.0", "--port", "9000"],
    )
    monkeypatch.setattr(cli.uvicorn, "run", fake_run)

    cli.api()

    assert captured["host"] == "0.0.0.0"
    assert captured["port"] == 9000
