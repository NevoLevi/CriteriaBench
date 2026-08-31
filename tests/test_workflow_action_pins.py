"""Reject mutable third-party action references in every workflow."""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_all_workflow_actions_are_full_sha_pinned() -> None:
    workflow_directory = ROOT / ".github" / "workflows"
    workflows = sorted([*workflow_directory.glob("*.yml"), *workflow_directory.glob("*.yaml")])

    assert workflows
    for workflow_path in workflows:
        workflow = workflow_path.read_text(encoding="utf-8")
        action_refs = re.findall(r"^\s*-?\s*uses:\s*([^\s#]+)", workflow, re.MULTILINE)
        remote_refs = [reference for reference in action_refs if not reference.startswith("./")]

        assert remote_refs, workflow_path
        assert all(re.fullmatch(r"[^@]+@[0-9a-f]{40}", reference) for reference in remote_refs), (
            workflow_path
        )
