"""Canonical serialization and read-once integrity checks for prediction bundles."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ValidationError

from criteriabench.domain.schemas import ClinicalTrialEligibility
from criteriabench.suite.loader import load_suite
from criteriabench.suite.models import LoadedSuite

from .models import (
    CURRENT_OUTPUT_SCHEMA_ID,
    CompletedPrediction,
    PredictionBundle,
    PredictionBundlePayload,
)

MAX_BUNDLE_BYTES = 100_000_000


def canonical_json_bytes(value: BaseModel | dict[str, Any] | list[Any]) -> bytes:
    """Return the single canonical JSON representation used by artifact hashes."""

    payload: Any
    if isinstance(value, BaseModel):
        payload = value.model_dump(mode="json")
    else:
        payload = value
    rendered = json.dumps(
        payload,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    return rendered.encode("utf-8")


def canonical_sha256(value: BaseModel | dict[str, Any] | list[Any]) -> str:
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def current_output_schema_sha256() -> str:
    """Hash the canonical current schema independently of bundle producer metadata."""

    return canonical_sha256(ClinicalTrialEligibility.model_json_schema())


def compute_suite_sha256(loaded: LoadedSuite) -> str:
    """Hash manifest identity and every ordered fixture identity into one suite ID."""

    identity = {
        "dataset_version": loaded.manifest.dataset_version,
        "manifest_sha256": loaded.manifest_sha256,
        "cases": [
            {
                "case_path": case.path,
                "case_sha256": case.sha256,
                "trial_id": case.fixture.trial.trial_id,
            }
            for case in loaded.cases
        ],
    }
    return canonical_sha256(identity)


def compute_bundle_sha256(bundle: PredictionBundle) -> str:
    body = bundle.model_dump(mode="json", exclude={"bundle_sha256"})
    return canonical_sha256(body)


def seal_bundle(payload: PredictionBundlePayload) -> PredictionBundle:
    """Add the deterministic hash to an already validated bundle body."""

    body = payload.model_dump(mode="json")
    return PredictionBundle.model_validate({**body, "bundle_sha256": canonical_sha256(body)})


def render_bundle(bundle: PredictionBundle) -> bytes:
    """Render a sealed bundle canonically, with one final LF for repository artifacts."""

    if compute_bundle_sha256(bundle) != bundle.bundle_sha256:
        raise ValueError("prediction bundle hash mismatch")
    return canonical_json_bytes(bundle) + b"\n"


def load_verified_bundle(
    bundle_path: Path, manifest_path: Path
) -> tuple[PredictionBundle, LoadedSuite]:
    """Read, strictly validate, and bind a canonical bundle to one local suite."""

    if not bundle_path.is_file():
        raise ValueError("prediction bundle does not exist")
    raw = bundle_path.read_bytes()
    if not raw or len(raw) > MAX_BUNDLE_BYTES:
        raise ValueError("prediction bundle size is outside the accepted boundary")
    try:
        bundle = PredictionBundle.model_validate_json(raw)
    except ValidationError as exc:
        raise ValueError("prediction bundle violates the strict v1 contract") from exc
    if raw != render_bundle(bundle):
        raise ValueError("prediction bundle is not canonical JSON")
    _verify_output_schema_binding(bundle)

    loaded = load_suite(manifest_path)
    _verify_dataset_binding(bundle, loaded)
    _verify_cases(bundle, loaded)
    return bundle, loaded


def _verify_output_schema_binding(bundle: PredictionBundle) -> None:
    binding = bundle.run.output_schema
    if binding.kind == "external":
        return
    if binding.schema_id != CURRENT_OUTPUT_SCHEMA_ID:
        raise ValueError("current output schema identifier mismatch")
    if binding.schema_sha256 != current_output_schema_sha256():
        raise ValueError("current output schema hash mismatch")


def _verify_dataset_binding(bundle: PredictionBundle, loaded: LoadedSuite) -> None:
    binding = bundle.dataset
    manifest = loaded.manifest
    if binding.dataset_version != manifest.dataset_version:
        raise ValueError("prediction bundle dataset version mismatch")
    if binding.manifest_name != Path(loaded.manifest_path).name:
        raise ValueError("prediction bundle manifest path mismatch")
    if binding.manifest_sha256 != loaded.manifest_sha256:
        raise ValueError("prediction bundle manifest hash mismatch")
    if binding.suite_sha256 != compute_suite_sha256(loaded):
        raise ValueError("prediction bundle suite hash mismatch")
    if binding.case_count != len(loaded.cases):
        raise ValueError("prediction bundle case count mismatch")


def _verify_cases(bundle: PredictionBundle, loaded: LoadedSuite) -> None:
    expected_paths = [case.path for case in loaded.cases]
    actual_paths = [case.case_path for case in bundle.cases]
    if actual_paths != expected_paths:
        expected = set(expected_paths)
        actual = set(actual_paths)
        if expected - actual:
            raise ValueError("prediction bundle is missing suite cases")
        if actual - expected:
            raise ValueError("prediction bundle contains extra suite cases")
        raise ValueError("prediction bundle case paths are out of order")

    for prediction_case, loaded_case in zip(bundle.cases, loaded.cases, strict=True):
        fixture = loaded_case.fixture
        if prediction_case.case_sha256 != loaded_case.sha256:
            raise ValueError(f"prediction bundle case hash mismatch: {loaded_case.path}")
        if prediction_case.trial_id != fixture.trial.trial_id:
            raise ValueError(f"prediction bundle trial ID mismatch: {loaded_case.path}")
        if isinstance(prediction_case, CompletedPrediction):
            _verify_completed_prediction(
                prediction_case, loaded_case.fixture.trial.eligibility_text
            )


def _verify_completed_prediction(case: CompletedPrediction, eligibility_text: str) -> None:
    prediction = case.prediction
    if prediction.trial_id != case.trial_id:
        raise ValueError(f"completed prediction returned the wrong trial ID: {case.case_path}")
    if case.prediction_sha256 != canonical_sha256(prediction):
        raise ValueError(f"completed prediction hash mismatch: {case.case_path}")
    for criterion in prediction.inclusion_criteria + prediction.exclusion_criteria:
        evidence = criterion.evidence
        if evidence.end_char > len(eligibility_text):
            raise ValueError(f"prediction evidence is outside source text: {case.case_path}")
        selected = eligibility_text[evidence.start_char : evidence.end_char]
        if selected != evidence.quote:
            raise ValueError(
                f"prediction evidence is not an exact source substring: {case.case_path}"
            )
