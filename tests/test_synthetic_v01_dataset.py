from __future__ import annotations

import hashlib
import json
from collections import Counter
from pathlib import Path
from typing import Any

from criteriabench.benchmark_cli import BenchmarkFixture
from criteriabench.evaluation.synthetic_v01 import (
    CASE_COUNT,
    DATASET_VERSION,
    FAMILIES,
    VARIANTS_PER_FAMILY,
    generate_cases,
    write_dataset,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DATASET_ROOT = PROJECT_ROOT / "data" / "synthetic_v0_1"


def _load_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_bytes())
    assert isinstance(payload, dict)
    return payload


def test_regeneration_is_byte_identical(tmp_path: Path) -> None:
    generated_root = tmp_path / "synthetic_v0_1"
    write_dataset(generated_root)

    expected_names = [f"case_{index:03d}.json" for index in range(1, CASE_COUNT + 1)]
    expected_names.append("manifest.json")
    assert sorted(path.name for path in generated_root.iterdir()) == sorted(expected_names)

    for filename in expected_names:
        assert (generated_root / filename).read_bytes() == (DATASET_ROOT / filename).read_bytes()


def test_all_cases_validate_and_evidence_spans_are_exact() -> None:
    cases = generate_cases()
    assert len(cases) == CASE_COUNT

    trial_ids: set[str] = set()
    family_counts: Counter[str] = Counter()
    slice_counts: Counter[str] = Counter()
    one_bullet_multi_label_cases = 0

    for index, payload in enumerate(cases, start=1):
        fixture = BenchmarkFixture.model_validate(payload)
        assert fixture.fixture_version == DATASET_VERSION
        assert fixture.trial.trial_id == f"CB-SYN-V01-{index:03d}"
        assert fixture.trial.trial_id not in trial_ids
        trial_ids.add(fixture.trial.trial_id)
        assert fixture.trial.source_url is None

        provenance = fixture.provenance
        assert provenance["kind"] == "synthetic"
        assert provenance["annotation_method"] == "deterministic_template_v0.1"
        assert provenance["review_status"] == "independent_review_pending"
        serialized = json.dumps(payload).casefold()
        assert "http://" not in serialized
        assert "https://" not in serialized
        assert "@" not in serialized

        family_counts[provenance["family"]] += 1
        slices = provenance["slices"].split(",")
        assert slices and all(slices)
        slice_counts.update(slices)

        assert fixture.reference is not None
        criteria = fixture.reference.inclusion_criteria + fixture.reference.exclusion_criteria
        assert len({criterion.criterion_id for criterion in criteria}) == len(criteria)
        for criterion in criteria:
            evidence = criterion.evidence
            selected = fixture.trial.eligibility_text[evidence.start_char : evidence.end_char]
            assert selected == evidence.quote == criterion.source_text

        if provenance["family"] in {"and_multi_clause", "or_multi_clause"}:
            one_bullet_multi_label_cases += 1
            assert len(criteria) == 2
            assert criteria[0].logic_group == criteria[1].logic_group
            assert criteria[0].logic_group.connector.value in {"and", "or"}
            bullet_count = sum(
                line.lstrip().startswith(("-", "*", "•"))
                for line in fixture.trial.eligibility_text.splitlines()
            )
            assert bullet_count == 1

    assert trial_ids == {f"CB-SYN-V01-{index:03d}" for index in range(1, CASE_COUNT + 1)}
    assert family_counts == Counter({family: VARIANTS_PER_FAMILY for family in FAMILIES})
    assert one_bullet_multi_label_cases == 2 * VARIANTS_PER_FAMILY
    assert slice_counts["one_bullet_multiple_labels"] == 2 * VARIANTS_PER_FAMILY


def test_manifest_pins_counts_slices_and_exact_fixture_bytes() -> None:
    manifest = _load_json(DATASET_ROOT / "manifest.json")
    assert manifest["dataset_version"] == DATASET_VERSION
    assert manifest["case_count"] == CASE_COUNT
    assert manifest["family_count"] == len(FAMILIES)
    assert manifest["variants_per_family"] == VARIANTS_PER_FAMILY
    assert manifest["clinical_validation"] is False
    assert manifest["license"] == "MIT"
    assert manifest["annotation"] == {
        "authoring_status": "single_author",
        "method": "deterministic_templates",
        "review_status": "independent_review_pending",
    }
    assert manifest["family_counts"] == {family: VARIANTS_PER_FAMILY for family in sorted(FAMILIES)}

    records = manifest["records"]
    assert isinstance(records, list)
    assert len(records) == CASE_COUNT
    expected_paths = [f"case_{index:03d}.json" for index in range(1, CASE_COUNT + 1)]
    assert [record["path"] for record in records] == expected_paths

    calculated_slice_counts: Counter[str] = Counter()
    for record in records:
        fixture_path = DATASET_ROOT / record["path"]
        raw = fixture_path.read_bytes()
        assert record["sha256"] == hashlib.sha256(raw).hexdigest()
        assert record["has_reference"] is True
        assert record["family"] in FAMILIES
        assert isinstance(record["slices"], list)
        assert all(isinstance(slice_name, str) for slice_name in record["slices"])
        calculated_slice_counts.update(record["slices"])
        BenchmarkFixture.model_validate_json(raw)

    assert manifest["slice_counts"] == dict(sorted(calculated_slice_counts.items()))
