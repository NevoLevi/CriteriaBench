from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]


def test_dependabot_uses_lock_aware_low_noise_update_policy() -> None:
    config = yaml.safe_load((ROOT / ".github" / "dependabot.yml").read_text(encoding="utf-8"))
    updates = {entry["package-ecosystem"]: entry for entry in config["updates"]}

    assert set(updates) == {"uv", "docker", "github-actions"}
    assert (ROOT / "uv.lock").is_file()
    assert updates["uv"]["versioning-strategy"] == "increase-if-necessary"

    for ecosystem, entry in updates.items():
        assert entry["directory"] == "/"
        assert entry["schedule"]["interval"] == "weekly"
        assert entry["open-pull-requests-limit"] <= 2
        assert "labels" not in entry
        assert entry["ignore"] == [
            {
                "dependency-name": "*",
                "update-types": ["version-update:semver-major"],
            }
        ]
        groups = list(entry["groups"].values())
        assert len(groups) == 1, ecosystem
        assert groups[0]["applies-to"] == "version-updates"
        assert groups[0]["patterns"] == ["*"]
        assert set(groups[0]["update-types"]) == {"minor", "patch"}
