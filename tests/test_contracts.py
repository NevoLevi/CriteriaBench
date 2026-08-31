from __future__ import annotations

from criteriabench.contracts import extraction_contract_hash


def test_contract_hash_is_stable_and_sensitive_to_model() -> None:
    first = extraction_contract_hash(provider="mock", model="rules-v1")
    second = extraction_contract_hash(provider="mock", model="rules-v1")
    changed = extraction_contract_hash(provider="mock", model="rules-v2")
    assert first == second
    assert changed != first
    assert len(first) == 64
