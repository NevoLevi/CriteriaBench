from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from criteriabench.benchmark_cli import _load_cases


async def test_verified_fixture_bytes_are_the_exact_bytes_parsed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = tmp_path / "fixture.json"
    approved = json.dumps(
        {
            "trial_id": "APPROVED-001",
            "title": "Approved fixture",
            "eligibility_text": "Inclusion Criteria:\n- Adult",
            "source_url": None,
        }
    ).encode()
    replacement = json.dumps(
        {
            "trial_id": "REPLACED-999",
            "title": "Changed after verification",
            "eligibility_text": "Inclusion Criteria:\n- Different content",
            "source_url": None,
        }
    ).encode()
    fixture.write_bytes(approved)
    resolved = fixture.resolve(strict=True)
    expected = {resolved: hashlib.sha256(approved).hexdigest()}
    original_read_bytes = Path.read_bytes
    fixture_reads = 0

    def changing_read_bytes(path: Path) -> bytes:
        nonlocal fixture_reads
        if path.resolve(strict=True) == resolved:
            fixture_reads += 1
            return approved if fixture_reads == 1 else replacement
        return original_read_bytes(path)

    monkeypatch.setattr(Path, "read_bytes", changing_read_bytes)

    cases = await _load_cases([fixture], expected_hashes=expected)

    assert fixture_reads == 1
    assert cases[0].fixture_sha256 == expected[resolved]
    assert cases[0].trial.trial_id == "APPROVED-001"
