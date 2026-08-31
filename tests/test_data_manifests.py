from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest


@pytest.mark.parametrize("directory", [Path("data/public"), Path("data/synthetic")])
def test_fixture_manifest_hashes_match_bytes(directory: Path) -> None:
    manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
    for record in manifest["records"]:
        payload = (directory / record["path"]).read_bytes()
        assert hashlib.sha256(payload).hexdigest() == record["sha256"]
