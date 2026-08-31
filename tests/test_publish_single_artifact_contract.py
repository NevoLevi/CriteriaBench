"""Ensure the scanned image bytes, not an independent rebuild, are published."""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_publish_scans_and_pushes_one_checksum_bound_archive() -> None:
    workflow = (ROOT / ".github/workflows/publish.yml").read_text(encoding="utf-8")
    verify, ghcr = workflow.split("\n  ghcr:", maxsplit=1)

    assert "needs: verify" in ghcr
    assert 'docker save --output "$archive"' in verify
    assert "input: artifacts/publish-image/criteriabench-image.tar" in verify
    assert "sha256sum --check artifacts/publish-image/SHA256SUMS" in verify
    assert verify.index("docker save") < verify.index("aquasecurity/trivy-action@")
    assert verify.index("aquasecurity/trivy-action@") < verify.index("actions/upload-artifact@")

    assert "actions/download-artifact@" in ghcr
    assert 'test "$actual_sha256" = "$EXPECTED_SHA256"' in ghcr
    assert 'docker load --input "$archive"' in ghcr
    assert "docker/build-push-action@" not in ghcr
    assert 'docker push "$tag"' in ghcr
    assert ghcr.index("docker load") < ghcr.index("docker/login-action@")
    assert "subject-digest: ${{ steps.push.outputs.digest }}" in ghcr


def test_every_publish_action_is_pinned_to_a_full_commit_sha() -> None:
    workflow = (ROOT / ".github/workflows/publish.yml").read_text(encoding="utf-8")
    action_refs = re.findall(r"^\s*-?\s*uses:\s*([^\s#]+)", workflow, re.MULTILINE)

    assert action_refs
    assert all(re.fullmatch(r"[^@]+@[0-9a-f]{40}", ref) for ref in action_refs)
