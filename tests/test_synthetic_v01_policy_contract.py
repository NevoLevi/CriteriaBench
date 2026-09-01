"""Policy and CI contracts for the committed synthetic v0.1 suite evidence."""

from __future__ import annotations

import json
from pathlib import Path

import yaml

from criteriabench.suite.loader import load_suite

ROOT = Path(__file__).resolve().parents[1]
POLICY_PATH = ROOT / "benchmarks" / "synthetic-v0.1-policy.json"
REPORT_PATH = ROOT / "docs" / "results" / "synthetic-v0.1.json"


def _json(path: Path) -> dict[str, object]:
    return json.loads(path.read_text(encoding="utf-8"))


def test_policy_declares_the_frozen_suite_and_primary_regression_gate() -> None:
    policy = _json(POLICY_PATH)

    assert policy["policy_version"] == "synthetic-v0.1-policy-v1"
    assert policy["configs"] == ["empty-v1", "rules-v1"]
    assert policy["dataset"] == {
        "case_count": 80,
        "declared_slices": [
            "between",
            "comparison",
            "consent",
            "demographic",
            "duration",
            "evidence_span",
            "exclusion",
            "format_variation",
            "inclusion",
            "laboratory",
            "logic_and",
            "logic_or",
            "multi_clause",
            "negation",
            "numeric_threshold",
            "one_bullet_multiple_labels",
            "punctuation",
            "range",
            "simple",
            "temporal",
        ],
        "family_count": 10,
        "hard_multi_label_case_count": 16,
        "version": "synthetic-v0.1",
    }
    assert policy["regression_gate"] == {
        "committed_json": "docs/results/synthetic-v0.1.json",
        "committed_markdown": "docs/results/synthetic-v0.1.md",
        "primary": "byte-for-byte-report-reproduction",
    }


def test_loader_enforces_fixture_hashes_and_source_bound_reference_evidence() -> None:
    policy = _json(POLICY_PATH)
    required = policy["required_reference_contract"]
    assert required == {
        "clinical_validation": False,
        "fixture_hashes_valid": True,
        "source_evidence_valid": True,
    }

    loaded = load_suite(ROOT / "data" / "synthetic_v0_1" / "manifest.json")
    assert len(loaded.cases) == 80
    assert loaded.manifest.clinical_validation is False
    assert all(len(case.sha256) == 64 for case in loaded.cases)


def test_committed_report_satisfies_structural_and_offline_policy() -> None:
    policy = _json(POLICY_PATH)
    report = _json(REPORT_PATH)
    dataset_policy = policy["dataset"]
    assert isinstance(dataset_policy, dict)
    dataset = report["dataset"]
    assert isinstance(dataset, dict)

    assert dataset["version"] == dataset_policy["version"]
    assert dataset["case_count"] == dataset_policy["case_count"]
    assert dataset["family_count"] == dataset_policy["family_count"]
    assert (
        dataset["slice_counts"]["one_bullet_multiple_labels"]
        == dataset_policy["hard_multi_label_case_count"]
    )
    assert sorted(dataset["slice_counts"]) == dataset_policy["declared_slices"]
    assert dataset["clinical_validation"] is False

    required = policy["required_execution"]
    assert isinstance(required, dict)
    baselines = report["baselines"]
    assert isinstance(baselines, list)
    by_config = {baseline["config"]: baseline for baseline in baselines}
    assert list(by_config) == policy["configs"]
    for baseline in by_config.values():
        assert baseline["completion_rate"] == required["completion_rate"]
        assert baseline["schema_valid_rate"] == required["schema_valid_rate"]
        assert baseline["paid"] is required["paid"]
        assert baseline["network"] is required["network"]
        assert baseline["input_tokens"] == required["input_tokens"]
        assert baseline["output_tokens"] == required["output_tokens"]
        assert baseline["estimated_cost_usd"] == required["estimated_cost_usd"]
        assert sorted(baseline["per_slice"]) == dataset_policy["declared_slices"]


def test_rules_baseline_clears_the_predeclared_improvement_gates() -> None:
    policy = _json(POLICY_PATH)
    report = _json(REPORT_PATH)
    gate = policy["improvement_gates"]
    assert isinstance(gate, dict)
    baselines = {baseline["config"]: baseline for baseline in report["baselines"]}
    challenger = baselines[gate["challenger"]]["all_cases"]
    reference = baselines[gate["reference"]]["all_cases"]

    assert challenger["mean_token_f1"] >= (
        reference["mean_token_f1"] + gate["minimum_mean_token_f1_delta"]
    )
    assert challenger["mean_macro_field_accuracy"] >= (
        reference["mean_macro_field_accuracy"] + gate["minimum_mean_macro_field_accuracy_delta"]
    )


def test_ci_reproduces_and_uploads_the_committed_suite_evidence() -> None:
    workflow_path = ROOT / ".github" / "workflows" / "ci.yml"
    workflow = yaml.safe_load(workflow_path.read_text(encoding="utf-8"))
    steps = workflow["jobs"]["test"]["steps"]
    names = [step.get("name") for step in steps]

    reproduce = steps[names.index("Reproduce synthetic v0.1 offline suite")]
    command = reproduce["run"]
    assert "uv run --frozen --no-env-file criteriabench-suite" in command
    assert "data/synthetic_v0_1/manifest.json" in command
    assert "--configs empty-v1 rules-v1" in command
    assert "--check-json docs/results/synthetic-v0.1.json" in command
    assert "--check-markdown docs/results/synthetic-v0.1.md" in command

    upload = steps[names.index("Upload synthetic v0.1 suite evidence")]
    assert upload["with"]["if-no-files-found"] == "error"
    assert upload["with"]["path"] == "artifacts/synthetic-v0.1.*"
    assert "github.run_id" in upload["with"]["name"]
    assert "github.run_attempt" in upload["with"]["name"]

    smoke = steps[names.index("Frozen synthetic benchmark smoke")]
    assert "data/synthetic/benchmark_case_001.json" in smoke["run"]
