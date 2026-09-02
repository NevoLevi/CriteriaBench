from __future__ import annotations

import hashlib
import json
from collections import Counter, defaultdict
from pathlib import Path

import pytest
from pydantic import ValidationError

from criteriabench.real.llf import (
    AGREEMENT_TRIAL_IDS,
    DISCLOSED_EXAMPLE_TRIAL_IDS,
    EXPECTED_MISSING_UPSTREAM_CASE_IDS,
    LlfAnnotation,
    LlfGenerationRecord,
    LlfImportError,
    _canonicalize_upstream_text,
    audit_llf,
    import_llf,
    load_llf_generation_records,
    load_llf_records,
    parse_llf_source,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
UPSTREAM_ROOT = PROJECT_ROOT / ".tools" / "upstream" / "leaf-corpora"
DATA_ROOT = PROJECT_ROOT / "data" / "real" / "llf"

EXPECTED_DISCLOSED_EXAMPLE_TRIAL_IDS = frozenset(
    {
        "NCT03860038",
        "NCT03860324",
        "NCT03862937",
        "NCT03865043",
        "NCT03925818",
    }
)

EXPECTED_GENERATION_CASES_SHA256 = (
    "ac7d9c0cf01158afb8b1ea6f8d320dc632b9211742296225d16308aa60884f84"
)
EXPECTED_SEMANTIC_SPLIT_SHA256 = "76dc8700ecc22e76fbaa14f0f2a5d749a49845397d4494b0cbc70a8e72724364"
EXPECTED_SEMANTIC_RECORD_SHA256 = {
    "records.jsonl": "b70749661de4b90eac0db5b61cb5b46dbe7e2f9230ce703da710950146860b62",
    "development_references.jsonl": (
        "569615cb7dd337419c2b3fcfae12765e6e475c85d7e1ba025df20c33025a8c04"
    ),
    "test_references.jsonl": ("cb1ea36903c85537ce5c889b62b78982e76180af757a01612e42993675b94615"),
    "agreement_annotations.jsonl": (
        "6a0c594c55c68852d66dd69dcd163ff02267c6eeb603c135ad146fcbf45b4aa0"
    ),
}


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _artifact_bytes(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def _semantic_record_sha256(path: Path) -> str:
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    semantic_rows = [
        {
            key: value.replace("\r\n", "\n") if isinstance(value, str) else value
            for key, value in row.items()
            if key
            not in {
                "reference_sha256",
                "source_file_bytes",
                "source_file_sha256",
            }
        }
        for row in rows
    ]
    payload = (
        json.dumps(
            semantic_rows,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode("utf-8")
    return _sha256(payload)


def _semantic_split_sha256(path: Path) -> str:
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload.pop("schema_version")
    serialized = (
        json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode("utf-8")
    return _sha256(serialized)


@pytest.mark.parametrize(
    "payload",
    [
        b"MIT License\n\nCopyright example\n",
        b"MIT License\r\n\r\nCopyright example\r\n",
        b"MIT License\r\n\nCopyright example\n",
    ],
)
def test_license_payload_is_canonical_lf_on_every_platform(payload: bytes) -> None:
    assert _canonicalize_upstream_text(payload, source_name="LICENSE") == (
        b"MIT License\n\nCopyright example\n"
    )


def test_license_payload_rejects_unsupported_bare_carriage_return() -> None:
    with pytest.raises(LlfImportError, match="unsupported bare carriage return"):
        _canonicalize_upstream_text(
            b"MIT License\rCopyright example\n",
            source_name="LICENSE",
        )


def test_repository_enforces_lf_for_generated_llf_artifacts() -> None:
    rules = {
        line.strip()
        for line in (PROJECT_ROOT / ".gitattributes").read_text(encoding="utf-8").splitlines()
    }
    assert "* text=auto eol=lf" in rules
    assert "data/real/llf/** text eol=lf" in rules


def test_upstream_text_normalization_rejects_invalid_utf8() -> None:
    with pytest.raises(LlfImportError, match="not valid UTF-8"):
        _canonicalize_upstream_text(b"criterion\n\xff", source_name="case.js")


def test_semantic_cases_references_and_split_remain_frozen() -> None:
    assert _sha256((DATA_ROOT / "generation_cases.jsonl").read_bytes()) == (
        EXPECTED_GENERATION_CASES_SHA256
    )
    assert _semantic_split_sha256(DATA_ROOT / "split_assignments.json") == (
        EXPECTED_SEMANTIC_SPLIT_SHA256
    )
    assert {
        name: _semantic_record_sha256(DATA_ROOT / name) for name in EXPECTED_SEMANTIC_RECORD_SHA256
    } == EXPECTED_SEMANTIC_RECORD_SHA256


def test_header_parser_decodes_strings_but_keeps_body_inert() -> None:
    payload = (
        b"'INC';\n\"raw\\ntext\";\n'aug\\x20text';\nthrow new Error(\"this must remain text\");"
    )

    polarity, raw_text, augmented_text, logical_form = parse_llf_source(payload)

    assert polarity == "inclusion"
    assert raw_text == "raw\ntext"
    assert augmented_text == "aug text"
    assert logical_form == 'throw new Error("this must remain text");'


def test_header_parser_retains_an_absent_upstream_reference() -> None:
    result = parse_llf_source(b"'EXC'\n'criterion'\n'augmented'\n\n")

    assert result == ("exclusion", "criterion", "augmented", None)


@pytest.mark.parametrize(
    "payload",
    [
        b"'OTHER'\n'criterion'\n'augmented'\ncond(\"x\")",
        b"'INC'\n''\n'augmented'\ncond(\"x\")",
        b"'INC'\n'criterion'\n'unterminated",
        b"x" * 16_385,
    ],
)
def test_header_parser_rejects_invalid_or_unbounded_input(payload: bytes) -> None:
    with pytest.raises(LlfImportError):
        parse_llf_source(payload)


def test_record_contract_never_allows_a_fabricated_missing_reference() -> None:
    record = {
        "dataset_id": "leaf-logical-forms",
        "dataset_version": "llf-461288a",
        "case_id": "NCT03868891_6",
        "trial_id": "NCT03868891",
        "criterion_index": 6,
        "split": "test",
        "polarity": "inclusion",
        "raw_text": "criterion",
        "augmented_text": "augmented",
        "reference_status": "missing_upstream",
        "logical_form": 'cond("invented")',
        "annotator_id": "annotator_2",
        "annotation_role": "primary",
        "source_path": ("leaf_logical_forms/annotator_2/batch8/NCT03868891_6.js"),
        "source_file_bytes": 100,
        "source_file_sha256": "a" * 64,
        "raw_text_sha256": _sha256(b"criterion"),
        "reference_sha256": _sha256(b'cond("invented")'),
    }

    with pytest.raises(ValidationError, match="must not invent"):
        LlfAnnotation.model_validate(record)


def test_generation_contract_rejects_reference_or_lineage_fields() -> None:
    source = {
        "case_id": "NCT00000001_1",
        "trial_id": "NCT00000001",
        "split": "development",
        "polarity": "inclusion",
        "source_text": "Age at least 18 years",
        "source_sha256": _sha256(b"Age at least 18 years"),
        "logical_form": "cond('forbidden')",
    }
    with pytest.raises(ValidationError, match="Extra inputs"):
        LlfGenerationRecord.model_validate(source)


def test_committed_records_preserve_counts_missing_cases_and_split_boundary() -> None:
    records = load_llf_records(DATA_ROOT / "records.jsonl")
    agreement = load_llf_records(DATA_ROOT / "agreement_annotations.jsonl")

    assert len(records) == 2_000
    assert len({record.case_id for record in records}) == 2_000
    assert len({record.trial_id for record in records}) == 885
    assert Counter(record.reference_status for record in records) == {
        "available": 1_997,
        "missing_upstream": 3,
    }
    missing = {record.case_id: record for record in records if record.logical_form is None}
    assert set(missing) == EXPECTED_MISSING_UPSTREAM_CASE_IDS
    assert all(record.reference_status == "missing_upstream" for record in missing.values())
    assert all(record.reference_sha256 is None for record in missing.values())

    development = [record for record in records if record.split == "development"]
    test = [record for record in records if record.split == "test"]
    assert (len(development), len(test)) == (200, 1_800)
    trial_counts = (
        len({record.trial_id for record in development}),
        len({record.trial_id for record in test}),
    )
    assert trial_counts == (
        86,
        799,
    )
    assert {record.trial_id for record in development}.isdisjoint(
        {record.trial_id for record in test}
    )
    assert DISCLOSED_EXAMPLE_TRIAL_IDS == EXPECTED_DISCLOSED_EXAMPLE_TRIAL_IDS
    forced = AGREEMENT_TRIAL_IDS | DISCLOSED_EXAMPLE_TRIAL_IDS
    assert all(record.split == "development" for record in records if record.trial_id in forced)

    assert len(agreement) == 60
    by_case: dict[str, list[LlfAnnotation]] = defaultdict(list)
    for record in agreement:
        by_case[record.case_id].append(record)
        assert record.split == "development"
        assert record.reference_status == "available"
    assert len(by_case) == 20
    assert all(
        len(rows) == 3
        and {row.annotator_id for row in rows} == {"annotator_1", "annotator_2", "annotator_3"}
        for rows in by_case.values()
    )


def test_committed_generation_artifact_is_physical_source_only_and_aligned() -> None:
    generation = load_llf_generation_records(DATA_ROOT / "generation_cases.jsonl")
    references = load_llf_records(DATA_ROOT / "records.jsonl")
    assert len(generation) == 2_000
    assert [record.case_id for record in generation] == [record.case_id for record in references]
    assert all(
        source.trial_id == reference.trial_id
        and source.split == reference.split
        and source.polarity == reference.polarity
        and source.source_text == reference.raw_text
        and source.source_sha256 == reference.raw_text_sha256
        for source, reference in zip(generation, references, strict=True)
    )
    assert set(generation[0].model_dump()) == {
        "case_id",
        "trial_id",
        "split",
        "polarity",
        "source_text",
        "source_sha256",
    }

    generation_manifest = json.loads((DATA_ROOT / "generation_manifest.json").read_bytes())
    assert generation_manifest["safety"] == {
        "source_only": True,
        "reference_availability_present": False,
        "missing_reference_identities_present": False,
        "scorable_counts_present": False,
        "reference_artifact_hashes_present": False,
    }
    assert {item["path"] for item in generation_manifest["artifacts"]} == {
        "generation_cases.jsonl",
        "split_assignments.json",
    }
    forbidden_keys = {
        "available_primary_references",
        "missing_upstream_case_ids",
        "missing_upstream_primary_references",
        "records_sha256",
        "reference_sha256",
        "scorable_case_count",
    }
    assert forbidden_keys.isdisjoint(generation_manifest)
    assert all(
        forbidden_keys.isdisjoint(value)
        for value in generation_manifest.values()
        if isinstance(value, dict)
    )

    development_references = load_llf_records(DATA_ROOT / "development_references.jsonl")
    test_references = load_llf_records(DATA_ROOT / "test_references.jsonl")
    assert len(development_references) == 200
    assert len(test_references) == 1_800
    assert all(record.split == "development" for record in development_references)
    assert all(record.split == "test" for record in test_references)
    recombined = tuple(
        sorted((*development_references, *test_references), key=lambda record: record.case_id)
    )
    assert recombined == references


def test_committed_manifest_seals_every_artifact_and_source_file() -> None:
    manifest_bytes = (DATA_ROOT / "manifest.json").read_bytes()
    manifest = json.loads(manifest_bytes)
    expected_manifest_hash, filename = (
        (DATA_ROOT / "manifest.sha256").read_text(encoding="ascii").split()
    )
    assert filename == "manifest.json"
    assert _sha256(manifest_bytes) == expected_manifest_hash

    canonical_payload_hash = manifest.pop("canonical_payload_sha256")
    canonical_payload = (
        json.dumps(manifest, ensure_ascii=False, separators=(",", ":"), sort_keys=True) + "\n"
    ).encode()
    assert _sha256(canonical_payload) == canonical_payload_hash

    for artifact in manifest["artifacts"]:
        payload = (DATA_ROOT / artifact["path"]).read_bytes()
        assert len(payload) == artifact["bytes"]
        assert _sha256(payload) == artifact["sha256"]

    license_payload = (DATA_ROOT / "LICENSE.upstream.txt").read_bytes()
    assert b"\r" not in license_payload

    source_rows = [
        json.loads(line)
        for line in (DATA_ROOT / "source_manifest.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert len(source_rows) == 2_060
    assert len({row["source_path"] for row in source_rows}) == 2_060
    assert all(len(row["source_file_sha256"]) == 64 for row in source_rows)

    split_document = json.loads((DATA_ROOT / "split_assignments.json").read_bytes())
    assignments = split_document["assignments"]
    assert len(assignments) == 885
    assert len({row["trial_id"] for row in assignments}) == 885
    assert manifest["split"]["assignment_sha256"] == _sha256(
        (DATA_ROOT / "split_assignments.json").read_bytes()
    )


@pytest.mark.skipif(
    not (UPSTREAM_ROOT / ".git" / "HEAD").is_file(),
    reason="pinned LLF clone absent",
)
def test_pinned_import_is_byte_reproducible_and_matches_committed_data(tmp_path: Path) -> None:
    audit = audit_llf(UPSTREAM_ROOT)
    assert audit.source_file_count == 2_060
    assert audit.primary_case_count == 2_000
    assert audit.available_primary_reference_count == 1_997
    assert audit.missing_upstream_primary_reference_count == 3
    assert set(audit.missing_upstream_case_ids) == EXPECTED_MISSING_UPSTREAM_CASE_IDS
    assert audit.agreement_file_count == 60
    assert audit.agreement_case_count == 20
    assert audit.trial_count == 885
    assert audit.duplicate_primary_case_count == 0

    first = tmp_path / "first"
    second = tmp_path / "second"
    import_llf(UPSTREAM_ROOT, first)
    import_llf(UPSTREAM_ROOT, second)

    assert _artifact_bytes(first) == _artifact_bytes(second)
    assert _artifact_bytes(first) == _artifact_bytes(DATA_ROOT)
