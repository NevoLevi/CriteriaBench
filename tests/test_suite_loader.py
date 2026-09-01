from __future__ import annotations

import hashlib
import json
import shutil
from collections import Counter
from pathlib import Path
from typing import Any

import pytest

from criteriabench.suite.loader import _confined_fixture_path, load_suite

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DATASET_ROOT = PROJECT_ROOT / "data" / "synthetic_v0_1"


def _copy_dataset(tmp_path: Path) -> Path:
    destination = tmp_path / "synthetic_v0_1"
    shutil.copytree(DATASET_ROOT, destination)
    return destination


def _read_object(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_bytes())
    assert isinstance(payload, dict)
    return payload


def _write_object(path: Path, payload: dict[str, Any]) -> bytes:
    raw = (json.dumps(payload, indent=2, sort_keys=True) + "\n").encode("utf-8")
    path.write_bytes(raw)
    return raw


def _update_hash(dataset: Path, index: int, raw: bytes) -> None:
    manifest_path = dataset / "manifest.json"
    manifest = _read_object(manifest_path)
    records = manifest["records"]
    assert isinstance(records, list)
    record = records[index]
    assert isinstance(record, dict)
    record["sha256"] = hashlib.sha256(raw).hexdigest()
    _write_object(manifest_path, manifest)


def test_loader_accepts_exact_v01_and_reads_each_json_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = Path.read_bytes
    reads: Counter[Path] = Counter()

    def counted(path: Path) -> bytes:
        resolved = path.resolve(strict=True)
        if DATASET_ROOT.resolve(strict=True) in (resolved, *resolved.parents):
            reads[resolved] += 1
        return original(path)

    monkeypatch.setattr(Path, "read_bytes", counted)
    loaded = load_suite(DATASET_ROOT / "manifest.json")

    assert len(loaded.cases) == 80
    assert len({case.fixture.trial.trial_id for case in loaded.cases}) == 80
    assert set(reads.values()) == {1}
    assert len(reads) == 81


def test_loader_path_confinement_rejects_traversal(tmp_path: Path) -> None:
    dataset = _copy_dataset(tmp_path)
    with pytest.raises(ValueError, match="escapes"):
        _confined_fixture_path(dataset.resolve(), "../case_001.json")


def test_loader_rejects_fixture_hash_mismatch(tmp_path: Path) -> None:
    dataset = _copy_dataset(tmp_path)
    fixture = dataset / "case_001.json"
    fixture.write_bytes(fixture.read_bytes() + b" ")
    with pytest.raises(ValueError, match="hash mismatch"):
        load_suite(dataset / "manifest.json")


def test_loader_rejects_duplicate_or_out_of_sequence_trial(tmp_path: Path) -> None:
    dataset = _copy_dataset(tmp_path)
    fixture_path = dataset / "case_002.json"
    fixture = _read_object(fixture_path)
    trial = fixture["trial"]
    reference = fixture["reference"]
    assert isinstance(trial, dict) and isinstance(reference, dict)
    trial["trial_id"] = "CB-SYN-V01-001"
    reference["trial_id"] = "CB-SYN-V01-001"
    raw = _write_object(fixture_path, fixture)
    _update_hash(dataset, 1, raw)
    with pytest.raises(ValueError, match=r"trial sequence|duplicate trial_id"):
        load_suite(dataset / "manifest.json")


def test_loader_rejects_inexact_evidence_substring(tmp_path: Path) -> None:
    dataset = _copy_dataset(tmp_path)
    fixture_path = dataset / "case_001.json"
    fixture = _read_object(fixture_path)
    trial = fixture["trial"]
    assert isinstance(trial, dict)
    trial["eligibility_text"] = "X" + str(trial["eligibility_text"])
    raw = _write_object(fixture_path, fixture)
    _update_hash(dataset, 0, raw)
    with pytest.raises(ValueError, match="exact source substring"):
        load_suite(dataset / "manifest.json")
