"""Immutability contract for the published Synthetic v0.1 report artifacts."""

from __future__ import annotations

import hashlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SYNTHETIC_V01_MANIFEST_SHA256 = "348ca04e94b00312d174c3011a2edfe7f65e731611536af636fc553bb4725508"
HISTORICAL_REPORT_HASHES = {
    "docs/results/synthetic-v0.1.json": (
        "70c90013c026723dc7641d279010025cf688ea84758d2adef04e9a6f7a265a3f"
    ),
    "docs/results/synthetic-v0.1.md": (
        "9691e84c743ac11dd369fa3556d2c358f029e711e7252267ba46e5d4c2c314ff"
    ),
}


def test_published_synthetic_v01_reports_remain_byte_immutable() -> None:
    """Corrections belong in v0.1.1; the already-published v0.1 bytes stay frozen."""


def test_synthetic_v01_manifest_remains_byte_immutable() -> None:
    manifest_path = ROOT / "data/synthetic_v0_1/manifest.json"
    assert manifest_path.is_file()
    assert hashlib.sha256(manifest_path.read_bytes()).hexdigest() == SYNTHETIC_V01_MANIFEST_SHA256

    for relative_path, expected_sha256 in HISTORICAL_REPORT_HASHES.items():
        report_path = ROOT / relative_path
        assert report_path.is_file()
        assert hashlib.sha256(report_path.read_bytes()).hexdigest() == expected_sha256


def test_byte_hashed_json_and_generated_reports_are_forced_to_lf() -> None:
    attributes = {
        line.strip()
        for line in (ROOT / ".gitattributes").read_text(encoding="utf-8").splitlines()
        if line.strip()
    }
    assert {
        "data/public/*.json text eol=lf",
        "data/synthetic/*.json text eol=lf",
        "data/synthetic_v0_1/*.json text eol=lf",
        "docs/results/synthetic-v0.1.json text eol=lf",
        "docs/results/synthetic-v0.1.md text eol=lf",
        "docs/results/synthetic-v0.1.1.json text eol=lf",
        "docs/results/synthetic-v0.1.1.md text eol=lf",
    } <= attributes
