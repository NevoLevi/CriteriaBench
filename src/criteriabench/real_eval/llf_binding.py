"""Exact, reference-free generation binding for the frozen LLF Real v1 corpus."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, cast

from criteriabench.real.graph_v2 import CriterionKindV2
from criteriabench.real.llf import (
    DATASET_ID,
    DATASET_VERSION,
    GENERATION_MANIFEST_SCHEMA_VERSION,
    IMPORT_SCHEMA_VERSION,
    SPLIT_ALGORITHM,
    LlfGenerationRecord,
    load_llf_generation_records_bytes,
)
from criteriabench.real_eval.integrity import case_set_sha256
from criteriabench.real_eval.models import GenerationCase, GenerationDatasetBinding

LLF_GENERATION_MANIFEST_SHA256 = "c67911011a906afe5e81c4f39310a765d899244a3c831f180111b3260ac9ce58"
LLF_GENERATION_CASES_SHA256 = "ac7d9c0cf01158afb8b1ea6f8d320dc632b9211742296225d16308aa60884f84"
LLF_SPLIT_ASSIGNMENTS_SHA256 = "2c00584303dbb653838eb21b1fec4eebae28ec3508f3acbddd333012391c68fc"
LlfEvaluationSplit = Literal["development", "test"]


class LlfBindingError(ValueError):
    """Raised when local LLF bytes do not match the frozen evaluation corpus."""


@dataclass(frozen=True, slots=True)
class LlfGenerationSplit:
    """Sanitized inputs plus their exact dataset binding; contains no references."""

    dataset: GenerationDatasetBinding
    cases: tuple[GenerationCase, ...]


def load_llf_generation_split(
    dataset_dir: Path,
    split: LlfEvaluationSplit,
) -> LlfGenerationSplit:
    """Verify frozen corpus bytes/splits and return source-only model inputs."""

    root = dataset_dir.resolve(strict=True)
    manifest_path = _contained_file(root, "generation_manifest.json")
    manifest_bytes = manifest_path.read_bytes()
    manifest_sha256 = _sha256(manifest_bytes)
    if manifest_sha256 != LLF_GENERATION_MANIFEST_SHA256:
        raise LlfBindingError("LLF generation manifest does not match the frozen digest")

    manifest = _json_object(manifest_bytes, "generation_manifest.json")
    _validate_manifest_identity(manifest)
    canonical_payload_sha256 = _required_str(manifest, "canonical_payload_sha256")
    payload = {key: value for key, value in manifest.items() if key != "canonical_payload_sha256"}
    if _sha256(_canonical_json_bytes(payload)) != canonical_payload_sha256:
        raise LlfBindingError("LLF canonical manifest payload hash mismatch")

    artifacts = _artifact_table(manifest)
    generation_bytes = _verify_artifact(
        root,
        artifacts,
        "generation_cases.jsonl",
        expected_sha256=LLF_GENERATION_CASES_SHA256,
    )
    assignments_bytes = _verify_artifact(
        root,
        artifacts,
        "split_assignments.json",
        expected_sha256=LLF_SPLIT_ASSIGNMENTS_SHA256,
    )
    records = load_llf_generation_records_bytes(
        generation_bytes,
        source_name="generation_cases.jsonl",
    )
    _validate_generation_against_manifest(records, manifest, artifacts)
    _validate_assignments(assignments_bytes, records, manifest, artifacts)

    selected = tuple(record for record in records if record.split == split)
    if not selected:
        raise LlfBindingError(f"LLF split is empty: {split}")
    cases = tuple(_generation_case(record) for record in selected)
    dataset = GenerationDatasetBinding(
        dataset_id=DATASET_ID,
        dataset_version=DATASET_VERSION,
        split=split,
        split_unit="trial_id",
        generation_manifest_sha256=manifest_sha256,
        generation_cases_sha256=LLF_GENERATION_CASES_SHA256,
        split_assignments_sha256=LLF_SPLIT_ASSIGNMENTS_SHA256,
        case_set_sha256=case_set_sha256(cases),
        case_count=len(cases),
    )
    return LlfGenerationSplit(dataset=dataset, cases=cases)


def _generation_case(record: LlfGenerationRecord) -> GenerationCase:
    kind = (
        CriterionKindV2.INCLUSION if record.polarity == "inclusion" else CriterionKindV2.EXCLUSION
    )
    return GenerationCase(
        case_id=record.case_id,
        trial_id=record.trial_id,
        document_id=record.case_id,
        criterion_kind=kind,
        source_text=record.source_text,
        source_sha256=record.source_sha256,
    )


def _validate_manifest_identity(manifest: dict[str, object]) -> None:
    expected = {
        "schema_version": GENERATION_MANIFEST_SCHEMA_VERSION,
        "dataset_id": DATASET_ID,
        "dataset_version": DATASET_VERSION,
    }
    for key, value in expected.items():
        if manifest.get(key) != value:
            raise LlfBindingError(f"LLF manifest has unexpected {key}")
    safety = _required_object(manifest, "safety")
    expected_safety = {
        "source_only": True,
        "reference_availability_present": False,
        "missing_reference_identities_present": False,
        "scorable_counts_present": False,
        "reference_artifact_hashes_present": False,
    }
    if safety != expected_safety:
        raise LlfBindingError("LLF generation manifest safety declaration is invalid")


def _validate_generation_against_manifest(
    records: tuple[LlfGenerationRecord, ...],
    manifest: dict[str, object],
    artifacts: dict[str, dict[str, object]],
) -> None:
    case_ids = [record.case_id for record in records]
    if len(case_ids) != len(set(case_ids)):
        raise LlfBindingError("generation_cases.jsonl contains duplicate case IDs")
    if case_ids != sorted(case_ids):
        raise LlfBindingError("generation_cases.jsonl is not in frozen case-ID order")

    if artifacts["generation_cases.jsonl"].get("record_count") != len(records):
        raise LlfBindingError("LLF generation artifact count mismatch")

    split_manifest = _required_object(manifest, "split")
    for split in ("development", "test"):
        cohort = tuple(record for record in records if record.split == split)
        frozen = _required_object(split_manifest, split)
        if frozen.get("cases") != len(cohort):
            raise LlfBindingError(f"LLF {split} case count mismatch")
        if frozen.get("trials") != len({record.trial_id for record in cohort}):
            raise LlfBindingError(f"LLF {split} trial count mismatch")
        cases = tuple(_generation_case(record) for record in cohort)
        if frozen.get("case_set_sha256") != case_set_sha256(cases):
            raise LlfBindingError(f"LLF {split} source case-set hash mismatch")


def _validate_assignments(
    raw: bytes,
    records: tuple[LlfGenerationRecord, ...],
    manifest: dict[str, object],
    artifacts: dict[str, dict[str, object]],
) -> None:
    assignment = _json_object(raw, "split_assignments.json")
    expected_fields = {
        "schema_version": IMPORT_SCHEMA_VERSION,
        "algorithm": SPLIT_ALGORITHM,
        "unit": "trial_id",
    }
    for key, value in expected_fields.items():
        if assignment.get(key) != value:
            raise LlfBindingError(f"LLF split assignments have unexpected {key}")
    rows = assignment.get("assignments")
    if not isinstance(rows, list):
        raise LlfBindingError("LLF split assignments must be a list")
    assignment_by_trial: dict[str, str] = {}
    for value in rows:
        if not isinstance(value, dict):
            raise LlfBindingError("LLF split assignment row must be an object")
        row = cast(dict[str, object], value)
        trial_id = _required_str(row, "trial_id")
        assigned_split = _required_str(row, "split")
        if assigned_split not in {"development", "test"}:
            raise LlfBindingError("LLF split assignment has an invalid split")
        if trial_id in assignment_by_trial:
            raise LlfBindingError("LLF split assignments contain a duplicate trial")
        assignment_by_trial[trial_id] = assigned_split

    record_split_by_trial: dict[str, str] = {}
    for record in records:
        previous = record_split_by_trial.setdefault(record.trial_id, record.split)
        if previous != record.split:
            raise LlfBindingError("LLF trial occurs in multiple record splits")
    if assignment_by_trial != record_split_by_trial:
        raise LlfBindingError("LLF split assignments do not match generation_cases.jsonl")
    if artifacts["split_assignments.json"].get("record_count") != len(rows):
        raise LlfBindingError("LLF split-assignment artifact count mismatch")
    if artifacts["split_assignments.json"].get("sha256") != _sha256(raw):
        raise LlfBindingError("LLF generation manifest split-assignment hash mismatch")


def _artifact_table(manifest: dict[str, object]) -> dict[str, dict[str, object]]:
    values = manifest.get("artifacts")
    if not isinstance(values, list):
        raise LlfBindingError("LLF manifest artifacts must be a list")
    table: dict[str, dict[str, object]] = {}
    for value in values:
        if not isinstance(value, dict):
            raise LlfBindingError("LLF artifact entry must be an object")
        artifact = cast(dict[str, object], value)
        name = _required_str(artifact, "path")
        if name in table:
            raise LlfBindingError("LLF manifest contains duplicate artifact paths")
        table[name] = artifact
    return table


def _verify_artifact(
    root: Path,
    artifacts: dict[str, dict[str, object]],
    name: str,
    *,
    expected_sha256: str,
) -> bytes:
    if name not in artifacts:
        raise LlfBindingError(f"LLF manifest omits {name}")
    path = _contained_file(root, name)
    raw = path.read_bytes()
    artifact = artifacts[name]
    if artifact.get("bytes") != len(raw):
        raise LlfBindingError(f"LLF artifact byte count mismatch: {name}")
    digest = _sha256(raw)
    if artifact.get("sha256") != digest or digest != expected_sha256:
        raise LlfBindingError(f"LLF artifact hash mismatch: {name}")
    return raw


def _contained_file(root: Path, name: str) -> Path:
    if Path(name).name != name:
        raise LlfBindingError("LLF artifact path must be a direct child")
    path = (root / name).resolve(strict=True)
    if path.parent != root or not path.is_file():
        raise LlfBindingError(f"LLF artifact resolves outside dataset directory: {name}")
    return path


def _json_object(raw: bytes, label: str) -> dict[str, object]:
    try:
        value = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise LlfBindingError(f"{label} is not valid UTF-8 JSON") from error
    if not isinstance(value, dict) or any(not isinstance(key, str) for key in value):
        raise LlfBindingError(f"{label} must contain one JSON object")
    return cast(dict[str, object], value)


def _required_object(value: dict[str, object], key: str) -> dict[str, object]:
    result = value.get(key)
    if not isinstance(result, dict) or any(not isinstance(item, str) for item in result):
        raise LlfBindingError(f"LLF field must be an object: {key}")
    return cast(dict[str, object], result)


def _required_str(value: dict[str, object], key: str) -> str:
    result = value.get(key)
    if not isinstance(result, str) or not result:
        raise LlfBindingError(f"LLF field must be a non-empty string: {key}")
    return result


def _canonical_json_bytes(value: object) -> bytes:
    return (
        json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode("utf-8")


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()
