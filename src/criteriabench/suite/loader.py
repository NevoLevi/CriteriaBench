"""Read-once, hash-pinned loader for the synthetic v0.1 suite."""

from __future__ import annotations

import hashlib
from pathlib import Path

from pydantic import ValidationError

from criteriabench.domain.schemas import CriterionKind
from criteriabench.suite.models import (
    DATASET_VERSION,
    EXPECTED_CASE_COUNT,
    DatasetManifest,
    LoadedCase,
    LoadedSuite,
    OfflineBenchmarkFixture,
    split_slices,
)


def load_suite(manifest_path: Path) -> LoadedSuite:
    """Load exactly the versioned 80-case suite through one verified byte read per file."""

    if manifest_path.name != "manifest.json":
        raise ValueError("suite input must be a synthetic v0.1 manifest.json")
    if not manifest_path.is_file():
        raise ValueError("suite manifest does not exist")

    raw_manifest = manifest_path.read_bytes()
    manifest_sha256 = hashlib.sha256(raw_manifest).hexdigest()
    try:
        manifest = DatasetManifest.model_validate_json(raw_manifest)
    except ValidationError as exc:
        raise ValueError("suite manifest does not match the synthetic v0.1 contract") from exc
    if manifest.dataset_version != DATASET_VERSION:
        raise ValueError("only the synthetic v0.1 manifest is accepted")

    dataset_root = manifest_path.parent.resolve(strict=True)
    loaded_cases: list[LoadedCase] = []
    trial_ids: set[str] = set()
    for index, record in enumerate(manifest.records, start=1):
        fixture_path = _confined_fixture_path(dataset_root, record.path)
        raw_fixture = fixture_path.read_bytes()
        actual_hash = hashlib.sha256(raw_fixture).hexdigest()
        if actual_hash != record.sha256:
            raise ValueError(f"fixture hash mismatch: {record.path}")
        try:
            fixture = OfflineBenchmarkFixture.model_validate_json(raw_fixture)
        except ValidationError as exc:
            raise ValueError(f"fixture violates the strict v0.1 contract: {record.path}") from exc

        expected_trial_id = f"CB-SYN-V01-{index:03d}"
        if fixture.trial.trial_id != expected_trial_id:
            raise ValueError(f"fixture trial sequence mismatch: {record.path}")
        if fixture.trial.trial_id in trial_ids:
            raise ValueError(f"duplicate trial_id: {fixture.trial.trial_id}")
        trial_ids.add(fixture.trial.trial_id)
        if fixture.provenance.family != record.family:
            raise ValueError(f"fixture family does not match manifest: {record.path}")
        slices = split_slices(fixture.provenance.slices)
        if slices != record.slice_names:
            raise ValueError(f"fixture slices do not match manifest: {record.path}")
        _validate_reference(fixture, record.path)
        loaded_cases.append(
            LoadedCase(
                path=record.path,
                sha256=actual_hash,
                family=record.family,
                slices=list(slices),
                fixture=fixture,
            )
        )

    if len(loaded_cases) != EXPECTED_CASE_COUNT or len(trial_ids) != EXPECTED_CASE_COUNT:
        raise ValueError("synthetic v0.1 must contain exactly 80 unique trials")
    return LoadedSuite(
        manifest_path=str(manifest_path.resolve(strict=True)),
        manifest_sha256=manifest_sha256,
        manifest=manifest,
        cases=loaded_cases,
    )


def _confined_fixture_path(dataset_root: Path, relative_path: str) -> Path:
    candidate = Path(relative_path)
    if candidate.is_absolute():
        raise ValueError("manifest fixture path must be relative")
    try:
        resolved = (dataset_root / candidate).resolve(strict=True)
        resolved.relative_to(dataset_root)
    except (OSError, ValueError) as exc:
        raise ValueError("manifest fixture path escapes its dataset") from exc
    if not resolved.is_file():
        raise ValueError(f"manifest fixture is not a file: {relative_path}")
    return resolved


def _validate_reference(fixture: OfflineBenchmarkFixture, path: str) -> None:
    reference = fixture.reference
    text = fixture.trial.eligibility_text
    for kind, criteria, prefix in (
        (CriterionKind.INCLUSION, reference.inclusion_criteria, "I"),
        (CriterionKind.EXCLUSION, reference.exclusion_criteria, "E"),
    ):
        expected_ids = [f"{prefix}{index:03d}" for index in range(1, len(criteria) + 1)]
        if [criterion.criterion_id for criterion in criteria] != expected_ids:
            raise ValueError(f"criterion IDs are not sequential: {path}")
        for criterion in criteria:
            if criterion.kind is not kind:
                raise ValueError(f"criterion kind is in the wrong list: {path}")
            evidence = criterion.evidence
            if evidence.end_char > len(text):
                raise ValueError(f"evidence span is outside eligibility text: {path}")
            selected = text[evidence.start_char : evidence.end_char]
            if selected != evidence.quote or selected != criterion.source_text:
                raise ValueError(f"evidence is not an exact source substring: {path}")
