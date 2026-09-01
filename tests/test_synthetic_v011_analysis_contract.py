"""Reader-facing and structural contracts added by offline-suite-v0.1.1."""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPORT_PATH = ROOT / "docs" / "results" / "synthetic-v0.1.1.json"
POLICY_PATH = ROOT / "benchmarks" / "synthetic-v0.1-policy.json"
FIELD_NAMES = {
    "category",
    "concept",
    "logic_connector",
    "negated",
    "operator",
    "temporal_relation",
    "unit",
    "value",
}
DENOMINATOR_BASES = {"reference_criteria", "predicted_criteria", "aligned_pairs"}


def _json(path: Path) -> dict[str, object]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(payload, dict)
    return payload


def test_v011_policy_pins_the_corrected_analysis_contract() -> None:
    policy = _json(POLICY_PATH)
    required = policy["required_analysis"]
    assert isinstance(required, dict)

    assert required["suite_version"] == "offline-suite-v0.1.1"
    assert required["base_template_count"] == 10
    assert required["case_lineage_count"] == 80
    assert required["lineage_derivation"] == "derived_from_manifest_family_and_record_order"
    assert set(required["mean_field_accuracies"]) == FIELD_NAMES
    assert required["family_cluster_sensitivity"] == {
        "cluster_count": 10,
        "resampling_unit": "family",
    }
    taxonomy = required["taxonomy"]
    assert isinstance(taxonomy, dict)
    assert taxonomy["events_can_overlap"] is True
    assert set(taxonomy["denominator_bases"]) == DENOMINATOR_BASES


def test_v011_report_exposes_lineage_fields_families_and_cluster_sensitivity() -> None:
    report = _json(REPORT_PATH)
    assert report["suite_version"] == "offline-suite-v0.1.1"
    dataset = report["dataset"]
    assert isinstance(dataset, dict)
    assert dataset["version"] == "synthetic-v0.1"
    assert dataset["base_template_count"] == 10
    assert dataset["lineage_derivation"] == "derived_from_manifest_family_and_record_order"
    assert "AI-assisted" in dataset["authoring_disclosure"]
    assert "independent" in dataset["authoring_disclosure"]

    lineage = dataset["lineage"]
    assert isinstance(lineage, list)
    assert len(lineage) == 80
    assert len({item["trial_id"] for item in lineage}) == 80
    assert len({item["base_template_id"] for item in lineage}) == 10
    assert {item["family_id"] for item in lineage} == set(dataset["family_counts"])

    family_names = set(dataset["family_counts"])
    baselines = report["baselines"]
    assert isinstance(baselines, list)
    for baseline in baselines:
        assert set(baseline["all_cases"]["mean_field_accuracies"]) == FIELD_NAMES
        assert set(baseline["per_family"]) == family_names
        assert set(baseline["leave_one_family_out"]) == family_names
        for interval in baseline["family_cluster_mean_metric_intervals"].values():
            assert interval["resampling_unit"] == "family"
            assert interval["cluster_count"] == 10
            assert "sensitivity" in interval["interpretation"].casefold()


def test_v011_taxonomy_rates_have_explicit_bases_and_overlap_disclosure() -> None:
    report = _json(REPORT_PATH)
    baselines = report["baselines"]
    assert isinstance(baselines, list)
    for baseline in baselines:
        taxonomy = baseline["taxonomy"]
        assert "overlap" in taxonomy["overlap_note"].casefold()
        assert set(taxonomy["metrics"]) == set(taxonomy["raw_counts"])
        for name, metric in taxonomy["metrics"].items():
            assert metric["denominator_basis"] in DENOMINATOR_BASES
            assert metric["count"] == taxonomy["raw_counts"][name]
            expected = (
                round(metric["count"] / metric["denominator"], 6) if metric["denominator"] else 0.0
            )
            assert metric["rate"] == expected


def test_current_public_docs_state_the_v011_interpretation_boundaries() -> None:
    paths = [
        ROOT / "README.md",
        ROOT / "docs" / "benchmark-methodology.md",
        ROOT / "data" / "synthetic_v0_1" / "README.md",
        ROOT / "docs" / "results" / "synthetic-v0.1.1.md",
    ]
    combined = "\n".join(path.read_text(encoding="utf-8") for path in paths)

    assert "exact criterion-text" in combined
    assert "10-family-cluster sensitivity" in combined
    assert "overlap" in combined
    assert "segmentation/grouping" in combined
    assert "general reasoning" in combined or "general evidence" in combined
    assert "AI-assisted" in combined
    assert "independent second-human" in combined
    assert "not research-grade" in combined
    assert "not clinically validated" in combined or "not clinical validation" in combined

    readme = paths[0].read_text(encoding="utf-8")
    assert "docs/results/synthetic-v0.1.1.md" in readme
    assert "docs/results/synthetic-v0.1.md" in readme
    methodology = paths[1].read_text(encoding="utf-8")
    assert "prediction-bundle-v1" in methodology
    assert "CI may hash-check and replay" in methodology
    assert "must never generate model predictions" in methodology
    assert "lower bounds" in methodology
    assert "not proof of provider billing" in methodology


def test_ci_keeps_model_generation_and_paid_credentials_outside_the_workflow() -> None:
    workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")

    assert 'ALLOW_PAID_CALLS: "false"' in workflow
    assert "OPENAI_API_KEY" not in workflow
    assert "--acknowledge-paid-api" not in workflow
    assert "--budget-usd" not in workflow
    assert "--live" not in workflow
