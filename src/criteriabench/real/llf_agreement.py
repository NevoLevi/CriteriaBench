"""Offline-only human annotation agreement sensitivity for the frozen LLF corpus.

The report is descriptive context for 20 selected, triply annotated criteria.
It is not clinical validation, a representative population estimate, or a
formal upper bound on model performance.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from itertools import combinations
from pathlib import Path
from typing import Any, Final, cast

import criteriabench.real.llf_semantics as llf_semantics_module
from criteriabench.real.llf import LlfAnnotation, load_llf_records_bytes
from criteriabench.real.llf_semantics import (
    PARSER_VERSION,
    PINNED_LLF_SEMANTIC_COVERAGE_SHA256,
    SEMANTIC_SCHEMA_VERSION,
    LlfMatchCounts,
    LlfSemanticComparison,
    LlfSemanticParseError,
    LlfSemanticReference,
    canonical_llf_scoring_sha256,
    compare_llf_semantics,
    parse_llf_semantic,
)

REPORT_SCHEMA_VERSION: Final = "llf-human-agreement-v1"
SCORER_ID: Final = "compare_llf_semantics:v1"
PINNED_AGREEMENT_FILENAME: Final = "agreement_annotations.jsonl"
PINNED_AGREEMENT_BYTES: Final = 67_804
PINNED_AGREEMENT_SHA256: Final = "3ffba65df1d2da0f46f43cbd8a2c96d8a7b333616f26c6c71d3d18e322677bad"
PINNED_PARSER_SOURCE_SHA256: Final = (
    "e133ef319d4736534f9631720a97b551ac8a8978e661a8b560436a4783e19f56"
)
EXPECTED_CASE_COUNT: Final = 20
EXPECTED_ANNOTATION_COUNT: Final = 60
EXPECTED_PARSED_ANNOTATION_COUNT: Final = 57
EXPECTED_MALFORMED_ANNOTATION_COUNT: Final = 3
EXPECTED_AVAILABLE_PAIR_COUNT: Final = 54
EXPECTED_UNAVAILABLE_PAIR_COUNT: Final = 6
EXPECTED_ANNOTATORS: Final = ("annotator_1", "annotator_2", "annotator_3")
_METRIC_NAMES: Final = ("nodes", "edges", "typed_components")
_KNOWN_MALFORMED: Final = (
    (
        "NCT03861962_2",
        "annotator_3",
        "malformed_expression",
        "5160f012fc082c60680b57868f90ea4ae9dc203161d60247ead1309b5655fcfc",
    ),
    (
        "NCT03862937_2",
        "annotator_2",
        "malformed_expression",
        "43c1af5e54bb8d13a5f6009407e9e7b0b7b36cb3742274f0306ac1aeb72b4220",
    ),
    (
        "NCT03927456_8",
        "annotator_3",
        "malformed_expression",
        "f164ffc782c27a0989fac80a2f37373130349f21fa00ad46acb73239275fe480",
    ),
)


class LlfAgreementDataError(ValueError):
    """The supplied agreement data or parser identity is not the frozen input."""


def _canonical_json_bytes(value: object, *, pretty: bool = False) -> bytes:
    if pretty:
        serialized = json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True)
    else:
        serialized = json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        )
    return (serialized + "\n").encode("utf-8")


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _payload_sha256(value: object) -> str:
    return _sha256(_canonical_json_bytes(value))


def _mean(values: Iterable[float]) -> float:
    materialized = tuple(values)
    if not materialized:
        raise LlfAgreementDataError("cannot summarize an empty metric denominator")
    return round(math.fsum(materialized) / len(materialized), 12)


def _normalized_parser_source_sha256() -> str:
    module_file = llf_semantics_module.__file__
    if module_file is None:
        raise LlfAgreementDataError("LLF parser implementation source is unavailable")
    source = Path(module_file).read_text(encoding="utf-8").replace("\r\n", "\n")
    return _sha256(source.encode("utf-8"))


def _parser_identity() -> dict[str, object]:
    observed_source_sha256 = _normalized_parser_source_sha256()
    if observed_source_sha256 != PINNED_PARSER_SOURCE_SHA256:
        raise LlfAgreementDataError("LLF parser implementation does not match the frozen pin")
    return {
        "version": PARSER_VERSION,
        "semantic_schema_version": SEMANTIC_SCHEMA_VERSION,
        "scorer_id": SCORER_ID,
        "implementation_source_sha256": observed_source_sha256,
        "semantic_coverage_artifact_sha256": PINNED_LLF_SEMANTIC_COVERAGE_SHA256,
        "execution": "never_compile_eval_exec_or_import",
    }


def _record_sort_key(record: LlfAnnotation) -> tuple[str, str, str]:
    return record.case_id, record.annotator_id, record.source_path


def _validate_and_order_records(
    records: Iterable[LlfAnnotation],
) -> tuple[LlfAnnotation, ...]:
    materialized = tuple(records)
    if len(materialized) != EXPECTED_ANNOTATION_COUNT:
        raise LlfAgreementDataError("agreement input must contain exactly 60 annotations")

    by_identity: dict[tuple[str, str], LlfAnnotation] = {}
    by_case: dict[str, list[LlfAnnotation]] = defaultdict(list)
    for record in materialized:
        if not isinstance(record, LlfAnnotation):
            raise TypeError("records must contain only LlfAnnotation objects")
        identity = (record.case_id, record.annotator_id)
        if identity in by_identity:
            raise LlfAgreementDataError(f"duplicate agreement annotation identity: {identity}")
        if record.annotation_role != "agreement":
            raise LlfAgreementDataError("agreement analysis cannot consume primary annotations")
        if record.split != "development":
            raise LlfAgreementDataError("agreement annotations must remain development-only")
        if not record.case_id.startswith(f"{record.trial_id}_"):
            raise LlfAgreementDataError(f"case and trial identity mismatch for {record.case_id}")
        if record.reference_status != "available":
            raise LlfAgreementDataError("all agreement annotations must have a reference")
        if record.logical_form is None or record.reference_sha256 is None:
            raise LlfAgreementDataError("an agreement annotation is missing its logical form")
        if _sha256(record.raw_text.encode("utf-8")) != record.raw_text_sha256:
            raise LlfAgreementDataError(f"criterion source bytes are tampered for {identity}")
        if _sha256(record.logical_form.encode("utf-8")) != record.reference_sha256:
            raise LlfAgreementDataError(f"logical-form bytes are tampered for {identity}")
        by_identity[identity] = record
        by_case[record.case_id].append(record)

    if len(by_case) != EXPECTED_CASE_COUNT:
        raise LlfAgreementDataError("agreement input must contain exactly 20 case identities")
    for case_id, case_records in sorted(by_case.items()):
        annotators = tuple(sorted(record.annotator_id for record in case_records))
        if annotators != EXPECTED_ANNOTATORS:
            raise LlfAgreementDataError(
                f"case {case_id} must have all three annotators exactly once"
            )
        source_bindings = {
            (
                record.trial_id,
                record.split,
                record.polarity,
                record.raw_text,
                record.raw_text_sha256,
            )
            for record in case_records
        }
        if len(source_bindings) != 1:
            raise LlfAgreementDataError(f"case {case_id} annotators disagree on source identity")

    ordered = tuple(sorted(materialized, key=_record_sort_key))
    canonical_bytes = b"".join(
        _canonical_json_bytes(record.model_dump(mode="json")) for record in ordered
    )
    if len(canonical_bytes) != PINNED_AGREEMENT_BYTES:
        raise LlfAgreementDataError("canonical agreement bytes do not match the frozen size")
    if _sha256(canonical_bytes) != PINNED_AGREEMENT_SHA256:
        raise LlfAgreementDataError("canonical agreement records do not match the frozen hash")
    return ordered


def _comparison_counts(comparison: LlfSemanticComparison) -> dict[str, LlfMatchCounts]:
    return {
        "nodes": comparison.nodes,
        "edges": comparison.edges,
        "typed_components": comparison.typed_components,
    }


def _directional_counts_payload(counts: LlfMatchCounts) -> dict[str, int | float]:
    return {
        "true_positive": counts.true_positive,
        "false_positive": counts.false_positive,
        "false_negative": counts.false_negative,
        "precision": round(counts.precision, 12),
        "recall": round(counts.recall, 12),
        "f1": round(counts.f1, 12),
    }


def _pair_metrics(
    a_to_b: LlfSemanticComparison,
    b_to_a: LlfSemanticComparison,
) -> dict[str, object]:
    a_counts = _comparison_counts(a_to_b)
    b_counts = _comparison_counts(b_to_a)
    metrics: dict[str, object] = {}
    for metric_name in _METRIC_NAMES:
        forward = a_counts[metric_name]
        reverse = b_counts[metric_name]
        if (
            forward.true_positive != reverse.true_positive
            or forward.false_positive != reverse.false_negative
            or forward.false_negative != reverse.false_positive
        ):
            raise LlfAgreementDataError("pairwise directional counts are not reciprocal")
        metrics[metric_name] = {
            "a_to_b": _directional_counts_payload(forward),
            "b_to_a": _directional_counts_payload(reverse),
            "symmetric": {
                "precision": _mean((forward.precision, reverse.precision)),
                "recall": _mean((forward.recall, reverse.recall)),
                "f1": _mean((forward.f1, reverse.f1)),
            },
        }
    return metrics


def _case_metric_summary(
    pair_rows: Sequence[Mapping[str, Any]],
) -> dict[str, object]:
    summary: dict[str, object] = {}
    for metric_name in _METRIC_NAMES:
        symmetric_rows = [
            cast(dict[str, float], pair["metrics"][metric_name]["symmetric"]) for pair in pair_rows
        ]
        summary[metric_name] = {
            "precision": _mean(row["precision"] for row in symmetric_rows),
            "recall": _mean(row["recall"] for row in symmetric_rows),
            "f1": _mean(row["f1"] for row in symmetric_rows),
        }
    return summary


def _case_macro_summary(case_rows: Sequence[Mapping[str, Any]]) -> dict[str, object]:
    metric_summary: dict[str, object] = {}
    for metric_name in _METRIC_NAMES:
        rows = [cast(dict[str, float], case["metrics"][metric_name]) for case in case_rows]
        metric_summary[metric_name] = {
            "precision": _mean(row["precision"] for row in rows),
            "recall": _mean(row["recall"] for row in rows),
            "f1": _mean(row["f1"] for row in rows),
        }
    return metric_summary


def build_llf_human_agreement_report(
    records: Iterable[LlfAnnotation],
) -> dict[str, object]:
    """Build a sealed case-macro agreement report from typed frozen annotations."""

    ordered = _validate_and_order_records(records)
    parser_identity = _parser_identity()
    parsed: dict[tuple[str, str], LlfSemanticReference] = {}
    parsed_rows: list[dict[str, object]] = []
    malformed_rows: list[dict[str, object]] = []
    by_case: dict[str, list[LlfAnnotation]] = defaultdict(list)

    for record in ordered:
        by_case[record.case_id].append(record)
        if record.logical_form is None or record.reference_sha256 is None:
            raise LlfAgreementDataError("validated agreement reference unexpectedly disappeared")
        try:
            semantic = parse_llf_semantic(record.logical_form, source_name=record.source_path)
        except LlfSemanticParseError as exc:
            malformed_rows.append(
                {
                    "case_id": record.case_id,
                    "annotator_id": record.annotator_id,
                    "source_path": record.source_path,
                    "reference_sha256": record.reference_sha256,
                    "error_code": exc.code,
                }
            )
            continue
        if semantic.source_sha256 != record.reference_sha256:
            raise LlfAgreementDataError(f"parsed reference hash mismatch for {record.source_path}")
        parsed[(record.case_id, record.annotator_id)] = semantic
        parsed_rows.append(
            {
                "case_id": record.case_id,
                "annotator_id": record.annotator_id,
                "source_path": record.source_path,
                "reference_sha256": record.reference_sha256,
                "canonical_scoring_sha256": canonical_llf_scoring_sha256(semantic),
                "node_count": len(semantic.nodes),
            }
        )

    observed_malformed = tuple(
        (
            str(row["case_id"]),
            str(row["annotator_id"]),
            str(row["error_code"]),
            str(row["reference_sha256"]),
        )
        for row in malformed_rows
    )
    if observed_malformed != _KNOWN_MALFORMED:
        raise LlfAgreementDataError("malformed agreement annotations changed from the frozen audit")
    if len(parsed_rows) != EXPECTED_PARSED_ANNOTATION_COUNT:
        raise LlfAgreementDataError(
            "parsed agreement annotation count changed from the frozen audit"
        )

    pair_rows: list[dict[str, object]] = []
    unavailable_pair_rows: list[dict[str, object]] = []
    pairs_by_case: dict[str, list[dict[str, object]]] = defaultdict(list)
    unavailable_by_case: dict[str, list[dict[str, object]]] = defaultdict(list)
    for case_id, case_records in sorted(by_case.items()):
        for record_a, record_b in combinations(case_records, 2):
            semantic_a = parsed.get((case_id, record_a.annotator_id))
            semantic_b = parsed.get((case_id, record_b.annotator_id))
            if semantic_a is None or semantic_b is None:
                malformed_annotators = sorted(
                    record.annotator_id
                    for record, semantic in ((record_a, semantic_a), (record_b, semantic_b))
                    if semantic is None
                )
                unavailable: dict[str, object] = {
                    "case_id": case_id,
                    "annotator_a": record_a.annotator_id,
                    "annotator_b": record_b.annotator_id,
                    "reason": "malformed_annotation",
                    "malformed_annotators": malformed_annotators,
                }
                unavailable_pair_rows.append(unavailable)
                unavailable_by_case[case_id].append(unavailable)
                continue

            a_to_b = compare_llf_semantics(semantic_a, semantic_b)
            b_to_a = compare_llf_semantics(semantic_b, semantic_a)
            if a_to_b.exact_match != b_to_a.exact_match:
                raise LlfAgreementDataError("canonical exact match is not symmetric")
            pair = {
                "case_id": case_id,
                "annotator_a": record_a.annotator_id,
                "annotator_b": record_b.annotator_id,
                "canonical_exact_match": a_to_b.exact_match,
                "canonical_scoring_sha256_a": canonical_llf_scoring_sha256(semantic_a),
                "canonical_scoring_sha256_b": canonical_llf_scoring_sha256(semantic_b),
                "metrics": _pair_metrics(a_to_b, b_to_a),
            }
            pair_rows.append(pair)
            pairs_by_case[case_id].append(pair)

    if len(pair_rows) != EXPECTED_AVAILABLE_PAIR_COUNT:
        raise LlfAgreementDataError("available pair count changed from the frozen audit")
    if len(unavailable_pair_rows) != EXPECTED_UNAVAILABLE_PAIR_COUNT:
        raise LlfAgreementDataError("unavailable pair count changed from the frozen audit")

    case_rows: list[dict[str, object]] = []
    for case_id, case_records in sorted(by_case.items()):
        available_pairs = pairs_by_case[case_id]
        if not available_pairs:
            raise LlfAgreementDataError(f"case {case_id} has no parseable pair")
        parsed_count = sum((case_id, record.annotator_id) in parsed for record in case_records)
        exact_rate = _mean(
            float(cast(bool, pair["canonical_exact_match"])) for pair in available_pairs
        )
        case_rows.append(
            {
                "case_id": case_id,
                "trial_id": case_records[0].trial_id,
                "parsed_annotation_count": parsed_count,
                "malformed_annotation_count": len(case_records) - parsed_count,
                "possible_pair_count": 3,
                "available_pair_count": len(available_pairs),
                "unavailable_pair_count": len(unavailable_by_case[case_id]),
                "available_pair_canonical_exact_match_rate": exact_rate,
                "three_annotation_full_exact_consensus": (
                    parsed_count == 3
                    and len(available_pairs) == 3
                    and all(cast(bool, pair["canonical_exact_match"]) for pair in available_pairs)
                ),
                "metrics": _case_metric_summary(available_pairs),
            }
        )

    parser_identity_sha256 = _payload_sha256(parser_identity)
    hashes = {
        "parsed_annotations_sha256": _payload_sha256(parsed_rows),
        "available_pairs_sha256": _payload_sha256(pair_rows),
        "unavailable_pairs_sha256": _payload_sha256(unavailable_pair_rows),
        "case_summaries_sha256": _payload_sha256(case_rows),
    }
    report: dict[str, object] = {
        "schema_version": REPORT_SCHEMA_VERSION,
        "dataset_id": "leaf-logical-forms",
        "dataset_version": "llf-461288a",
        "intended_use": "human_human_context_sensitivity_only",
        "inputs": {
            "agreement_annotations": {
                "path": PINNED_AGREEMENT_FILENAME,
                "bytes": PINNED_AGREEMENT_BYTES,
                "sha256": PINNED_AGREEMENT_SHA256,
                "annotation_count": EXPECTED_ANNOTATION_COUNT,
                "case_count": EXPECTED_CASE_COUNT,
                "annotations_per_case": 3,
            },
            "parser": parser_identity,
            "parser_identity_sha256": parser_identity_sha256,
        },
        "methodology": {
            "pairing": "all_three_unordered_annotator_pairs_within_each_case",
            "pair_orientation": "annotator_a_and_annotator_b_are_ascending_annotator_ids",
            "directional_metrics": "both_a_to_b_and_b_to_a",
            "symmetric_metric": "mean_of_both_directional_scores",
            "headline_weighting": "mean_pairs_within_case_then_equal_weight_mean_across_cases",
            "malformed_policy": "pairs_touching_a_malformed_annotation_are_unavailable_not_zero",
            "canonical_exact": "canonical_llf_scoring_sha256_equality",
            "partial_metrics": list(_METRIC_NAMES),
            "hash_canonicalization": "utf8_sorted_compact_json_with_trailing_lf",
        },
        "coverage": {
            "case_count": len(case_rows),
            "annotation_count": len(ordered),
            "parsed_annotation_count": len(parsed_rows),
            "malformed_annotation_count": len(malformed_rows),
            "possible_pair_count": EXPECTED_CASE_COUNT * 3,
            "available_pair_count": len(pair_rows),
            "unavailable_pair_count": len(unavailable_pair_rows),
            "fully_parseable_case_count": sum(
                cast(int, case["parsed_annotation_count"]) == 3 for case in case_rows
            ),
            "partially_parseable_case_count": sum(
                cast(int, case["parsed_annotation_count"]) == 2 for case in case_rows
            ),
        },
        "case_macro_summary": {
            "denominator_cases": len(case_rows),
            "available_pair_canonical_exact_match_rate": _mean(
                cast(float, case["available_pair_canonical_exact_match_rate"]) for case in case_rows
            ),
            "three_annotation_full_exact_consensus_case_count": sum(
                cast(bool, case["three_annotation_full_exact_consensus"]) for case in case_rows
            ),
            "metrics": _case_macro_summary(case_rows),
        },
        "malformed_annotations": malformed_rows,
        "parsed_annotations": parsed_rows,
        "available_pairs": pair_rows,
        "unavailable_pairs": unavailable_pair_rows,
        "case_summaries": case_rows,
        "hashes": hashes,
        "limitations": [
            "The 20 cases are a selected agreement subset, not a representative sample.",
            "Agreement measures consistency of LLF annotations, not clinical correctness.",
            (
                "Malformed annotations reduce available pairs and are disclosed rather than "
                "scored as disagreement."
            ),
            (
                "These descriptive human-human values are context, not clinical validation "
                "or a formal model ceiling."
            ),
        ],
    }
    report["canonical_payload_sha256"] = _payload_sha256(report)
    return report


def build_llf_human_agreement_report_from_path(
    agreement_path: Path,
) -> dict[str, object]:
    """Read one explicit pinned agreement path once and build the offline report."""

    if agreement_path.name != PINNED_AGREEMENT_FILENAME:
        raise LlfAgreementDataError(f"agreement path must be named {PINNED_AGREEMENT_FILENAME}")
    agreement_bytes = agreement_path.read_bytes()
    if len(agreement_bytes) != PINNED_AGREEMENT_BYTES:
        raise LlfAgreementDataError("agreement artifact byte count does not match the frozen pin")
    if _sha256(agreement_bytes) != PINNED_AGREEMENT_SHA256:
        raise LlfAgreementDataError("agreement artifact does not match the frozen hash")
    records = load_llf_records_bytes(agreement_bytes, source_name=str(agreement_path))
    return build_llf_human_agreement_report(records)


def llf_human_agreement_report_bytes(agreement_path: Path) -> bytes:
    """Return deterministic pretty JSON for one explicit pinned input path."""

    return _canonical_json_bytes(
        build_llf_human_agreement_report_from_path(agreement_path),
        pretty=True,
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("agreement", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args(list(argv) if argv is not None else None)
    agreement_path = cast(Path, args.agreement)
    output_path = cast(Path | None, args.output)
    check = cast(bool, args.check)
    payload = llf_human_agreement_report_bytes(agreement_path)
    if check:
        if output_path is None:
            parser.error("--check requires --output")
        if output_path.read_bytes() != payload:
            raise SystemExit("human agreement artifact does not reproduce byte-for-byte")
        return 0
    if output_path is None:
        sys.stdout.buffer.write(payload)
    else:
        output_path.write_bytes(payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "EXPECTED_ANNOTATION_COUNT",
    "EXPECTED_AVAILABLE_PAIR_COUNT",
    "EXPECTED_CASE_COUNT",
    "EXPECTED_MALFORMED_ANNOTATION_COUNT",
    "EXPECTED_PARSED_ANNOTATION_COUNT",
    "EXPECTED_UNAVAILABLE_PAIR_COUNT",
    "PINNED_AGREEMENT_SHA256",
    "PINNED_PARSER_SOURCE_SHA256",
    "REPORT_SCHEMA_VERSION",
    "SCORER_ID",
    "LlfAgreementDataError",
    "build_llf_human_agreement_report",
    "build_llf_human_agreement_report_from_path",
    "llf_human_agreement_report_bytes",
    "main",
]
