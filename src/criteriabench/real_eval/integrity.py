"""Canonical hashing, sealing, and source-bound verification for Real v1 artifacts."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from typing import Any

from pydantic import BaseModel

from criteriabench.real.graph_v2 import (
    canonical_graph_sha256,
    flat_graph_strict_json_schema,
    validate_evidence,
)
from criteriabench.real_eval.models import (
    CompletedPrediction,
    FrozenProtocol,
    FrozenProtocolPayload,
    GenerationCase,
    PredictionBundle,
    PredictionBundlePayload,
    ReferenceCase,
)


def canonical_json_bytes(value: BaseModel | dict[str, Any] | list[Any]) -> bytes:
    payload: Any = value.model_dump(mode="json") if isinstance(value, BaseModel) else value
    return json.dumps(
        payload,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def canonical_sha256(value: BaseModel | dict[str, Any] | list[Any]) -> str:
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def source_sha256(source_text: str) -> str:
    return hashlib.sha256(source_text.encode("utf-8")).hexdigest()


def seal_protocol(payload: FrozenProtocolPayload) -> FrozenProtocol:
    body = payload.model_dump(mode="json")
    return FrozenProtocol.model_validate({**body, "protocol_sha256": canonical_sha256(body)})


def verify_protocol(protocol: FrozenProtocol) -> None:
    body = protocol.model_dump(mode="json", exclude={"protocol_sha256"})
    if protocol.protocol_sha256 != canonical_sha256(body):
        raise ValueError("protocol hash mismatch")


def case_set_sha256(cases: Sequence[GenerationCase | ReferenceCase]) -> str:
    """Bind ordered source-only case identities; references never affect generation identity."""

    identities = [
        {
            "case_id": case.case_id,
            "trial_id": case.trial_id,
            "document_id": case.document_id,
            "criterion_kind": case.criterion_kind.value,
            "source_sha256": case.source_sha256,
        }
        for case in cases
    ]
    return canonical_sha256(identities)


def validate_generation_cases(cases: Sequence[GenerationCase]) -> None:
    if not cases:
        raise ValueError("generation requires at least one case")
    case_ids = [case.case_id for case in cases]
    if len(case_ids) != len(set(case_ids)):
        raise ValueError("generation cases contain duplicate case IDs")
    for case in cases:
        if source_sha256(case.source_text) != case.source_sha256:
            raise ValueError(f"generation source hash mismatch: {case.case_id}")


def validate_reference_cases(cases: Sequence[ReferenceCase]) -> None:
    validate_generation_cases(cases)
    for case in cases:
        reference = case.reference
        if reference is None:
            continue
        if reference.criterion_id != case.case_id:
            raise ValueError(f"reference criterion ID mismatch: {case.case_id}")
        if reference.criterion_kind != case.criterion_kind:
            raise ValueError(f"reference criterion kind mismatch: {case.case_id}")
        if reference.source.trial_id != case.trial_id:
            raise ValueError(f"reference trial ID mismatch: {case.case_id}")
        if reference.source.document_id != case.document_id:
            raise ValueError(f"reference document ID mismatch: {case.case_id}")
        validate_evidence(reference, case.source_text)
        if canonical_graph_sha256(reference) != case.reference_sha256:
            raise ValueError(f"reference graph hash mismatch: {case.case_id}")


def seal_bundle(payload: PredictionBundlePayload) -> PredictionBundle:
    body = payload.model_dump(mode="json")
    return PredictionBundle.model_validate({**body, "bundle_sha256": canonical_sha256(body)})


def verify_bundle(
    bundle: PredictionBundle,
    cases: Sequence[GenerationCase | ReferenceCase],
) -> None:
    body = bundle.model_dump(mode="json", exclude={"bundle_sha256"})
    if canonical_sha256(body) != bundle.bundle_sha256:
        raise ValueError("prediction bundle hash mismatch")
    expected_output_schema = canonical_sha256(flat_graph_strict_json_schema())
    if bundle.run.output_schema_sha256 != expected_output_schema:
        raise ValueError("bundle output schema hash does not match FlatGraphOutputV2")
    if bundle.dataset.case_count != len(cases):
        raise ValueError("bundle case count does not match local cases")
    if bundle.dataset.case_set_sha256 != case_set_sha256(cases):
        raise ValueError("bundle case-set hash mismatch")
    expected_ids = [case.case_id for case in cases]
    actual_ids = [case.case_id for case in bundle.cases]
    if actual_ids != expected_ids:
        raise ValueError("bundle case order or membership mismatch")
    for prediction, case in zip(bundle.cases, cases, strict=True):
        if (
            prediction.trial_id != case.trial_id
            or prediction.document_id != case.document_id
            or prediction.source_sha256 != case.source_sha256
        ):
            raise ValueError(f"bundle source identity mismatch: {case.case_id}")
        if isinstance(prediction, CompletedPrediction):
            if canonical_graph_sha256(prediction.prediction) != prediction.graph_sha256:
                raise ValueError(f"prediction graph hash mismatch: {case.case_id}")
            validate_evidence(prediction.prediction, case.source_text)


def render_bundle(bundle: PredictionBundle) -> bytes:
    verify_bundle_hash(bundle)
    return canonical_json_bytes(bundle) + b"\n"


def verify_bundle_hash(bundle: PredictionBundle) -> None:
    body = bundle.model_dump(mode="json", exclude={"bundle_sha256"})
    if bundle.bundle_sha256 != canonical_sha256(body):
        raise ValueError("prediction bundle hash mismatch")
