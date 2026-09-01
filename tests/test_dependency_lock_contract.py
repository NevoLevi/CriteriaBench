from __future__ import annotations

import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
UV_VERSION = "0.12.8"
UV_COMPATIBILITY = ">=0.12.7,<0.13"
SETUP_UV_SHA = "20cfd1bf945f4377ade1205e4dbc17946fc9a30d"


def _read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_lock_matches_project_and_contains_runtime_and_dev_dependencies() -> None:
    lock_path = ROOT / "uv.lock"
    assert lock_path.is_file()

    lock = tomllib.loads(lock_path.read_text(encoding="utf-8"))
    assert lock["version"] == 1
    assert lock["requires-python"] == ">=3.12"

    package_names = {package["name"] for package in lock["package"]}
    assert {
        "criteriabench",
        "fastapi",
        "mypy",
        "openai",
        "pytest",
        "ruff",
        "sqlalchemy",
    } <= package_names

    pyproject = tomllib.loads(_read("pyproject.toml"))
    assert pyproject["tool"]["uv"]["required-version"] == UV_COMPATIBILITY


def test_container_installs_runtime_from_the_frozen_lock() -> None:
    dockerfile = _read("Dockerfile")

    assert f"ghcr.io/astral-sh/uv:{UV_VERSION}@sha256:" in dockerfile
    assert "COPY pyproject.toml uv.lock README.md ./" in dockerfile
    assert "uv sync --frozen --no-dev --no-editable --no-install-project" in dockerfile
    assert "uv sync --frozen --no-dev --no-editable" in dockerfile
    assert "UV_PYTHON_DOWNLOADS=never" in dockerfile
    assert "pip install" not in dockerfile
    assert "python -m pip uninstall --yes pip" in dockerfile


def test_automation_verifies_and_consumes_lock_without_dotenv_discovery() -> None:
    for workflow_path in (".github/workflows/ci.yml", ".github/workflows/publish.yml"):
        workflow = _read(workflow_path)
        assert f"astral-sh/setup-uv@{SETUP_UV_SHA}" in workflow
        assert f'version: "{UV_VERSION}"' in workflow
        assert 'UV_NO_ENV_FILE: "1"' in workflow
        assert "UV_PYTHON_DOWNLOADS: never" in workflow
        assert "uv lock --check" in workflow
        assert "uv sync --frozen --extra dev" in workflow
        assert "uv run --frozen --no-env-file" in workflow
        assert "criteriabench-benchmark" in workflow
        assert "data/synthetic/benchmark_case_001.json" in workflow
        assert "pip install" not in workflow


def test_docker_context_excludes_dotenv_but_includes_lock() -> None:
    ignored = {
        line.strip()
        for line in _read(".dockerignore").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    }
    assert {".env", ".env.*"} <= ignored
    assert "uv.lock" not in ignored


def test_ci_uploads_unique_synthetic_benchmark_evidence() -> None:
    workflow = _read(".github/workflows/ci.yml")
    assert "Upload synthetic benchmark evidence" in workflow
    assert "github.run_id" in workflow
    assert "github.run_attempt" in workflow
    assert "if-no-files-found: error" in workflow
