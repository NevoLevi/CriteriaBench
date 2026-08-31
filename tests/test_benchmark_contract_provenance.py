from __future__ import annotations

import inspect
from typing import Any

import pytest

import criteriabench.benchmark_cli as benchmark_cli
from criteriabench.config import Settings


def test_source_hash_canonicalization_is_independent_of_newline_style() -> None:
    expected = "first\nsecond\nthird\n"
    assert benchmark_cli._canonical_source("first\r\nsecond\rthird\n") == expected
    assert benchmark_cli._canonical_source(expected) == expected


def test_openai_adapter_source_changes_extraction_contract(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = Settings(
        _env_file=None,
        LLM_PROVIDER="openai",
        ALLOW_PAID_CALLS=False,
    )
    baseline = benchmark_cli._contract_hash(settings)
    original_getsource = inspect.getsource

    def mutated_getsource(target: Any) -> str:
        source = original_getsource(target)
        if target is benchmark_cli.openai_module:
            return f"{source}\n# simulated adapter implementation change\n"
        return source

    monkeypatch.setattr(benchmark_cli.inspect, "getsource", mutated_getsource)

    assert benchmark_cli._contract_hash(settings) != baseline
