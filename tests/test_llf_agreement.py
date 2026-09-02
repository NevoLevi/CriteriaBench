from __future__ import annotations

import ast
import hashlib
import json
import math
from pathlib import Path
from typing import Any, cast

import pytest

import criteriabench.real.llf_agreement as llf_agreement
import criteriabench.real.llf_semantics as llf_semantics
from criteriabench.real.llf import load_llf_records
from criteriabench.real.llf_agreement import (
    EXPECTED_ANNOTATION_COUNT,
    EXPECTED_AVAILABLE_PAIR_COUNT,
    EXPECTED_CASE_COUNT,
    EXPECTED_MALFORMED_ANNOTATION_COUNT,
    EXPECTED_PARSED_ANNOTATION_COUNT,
    EXPECTED_UNAVAILABLE_PAIR_COUNT,
    PINNED_AGREEMENT_SHA256,
    PINNED_PARSER_SOURCE_SHA256,
    LlfAgreementDataError,
    build_llf_human_agreement_report,
    build_llf_human_agreement_report_from_path,
    llf_human_agreement_report_bytes,
    main,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
AGREEMENT_PATH = PROJECT_ROOT / "data" / "real" / "llf" / "agreement_annotations.jsonl"
REPORT_PATH = PROJECT_ROOT / "docs" / "results" / "llf-human-agreement.json"


def _pretty_bytes(value: object) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode()


def _payload_sha256(value: object) -> str:
    serialized = json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    return hashlib.sha256((serialized + "\n").encode()).hexdigest()


def test_real_agreement_coverage_and_known_malformed_rows_are_explicit() -> None:
    report = build_llf_human_agreement_report_from_path(AGREEMENT_PATH)
    coverage = cast(dict[str, int], report["coverage"])

    assert coverage == {
        "case_count": EXPECTED_CASE_COUNT,
        "annotation_count": EXPECTED_ANNOTATION_COUNT,
        "parsed_annotation_count": EXPECTED_PARSED_ANNOTATION_COUNT,
        "malformed_annotation_count": EXPECTED_MALFORMED_ANNOTATION_COUNT,
        "possible_pair_count": 60,
        "available_pair_count": EXPECTED_AVAILABLE_PAIR_COUNT,
        "unavailable_pair_count": EXPECTED_UNAVAILABLE_PAIR_COUNT,
        "fully_parseable_case_count": 17,
        "partially_parseable_case_count": 3,
    }
    malformed = cast(list[dict[str, Any]], report["malformed_annotations"])
    assert {(row["case_id"], row["annotator_id"], row["error_code"]) for row in malformed} == {
        ("NCT03861962_2", "annotator_3", "malformed_expression"),
        ("NCT03862937_2", "annotator_2", "malformed_expression"),
        ("NCT03927456_8", "annotator_3", "malformed_expression"),
    }
    case_rows = cast(list[dict[str, Any]], report["case_summaries"])
    assert len(case_rows) == 20
    assert {row["available_pair_count"] for row in case_rows} == {1, 3}
    assert sum(cast(int, row["available_pair_count"]) for row in case_rows) == 54


def test_pair_metrics_are_reciprocal_and_symmetric() -> None:
    report = build_llf_human_agreement_report_from_path(AGREEMENT_PATH)
    pair_rows = cast(list[dict[str, Any]], report["available_pairs"])
    assert len(pair_rows) == 54

    for pair in pair_rows:
        assert pair["annotator_a"] < pair["annotator_b"]
        for metric in cast(dict[str, dict[str, Any]], pair["metrics"]).values():
            a_to_b = cast(dict[str, float | int], metric["a_to_b"])
            b_to_a = cast(dict[str, float | int], metric["b_to_a"])
            symmetric = cast(dict[str, float], metric["symmetric"])
            assert a_to_b["true_positive"] == b_to_a["true_positive"]
            assert a_to_b["false_positive"] == b_to_a["false_negative"]
            assert a_to_b["false_negative"] == b_to_a["false_positive"]
            true_positive = int(a_to_b["true_positive"])
            a_false_positive = int(a_to_b["false_positive"])
            a_false_negative = int(a_to_b["false_negative"])
            precision_a = true_positive / (true_positive + a_false_positive)
            precision_b = true_positive / (true_positive + a_false_negative)
            f1_denominator = 2 * true_positive + a_false_positive + a_false_negative
            f1 = 2 * true_positive / f1_denominator
            assert symmetric["precision"] == round(
                math.fsum((precision_a, precision_b)) / 2,
                12,
            )
            assert symmetric["recall"] == round(
                math.fsum((precision_b, precision_a)) / 2,
                12,
            )
            assert symmetric["f1"] == round(f1, 12)


def test_headline_summary_averages_pairs_within_case_then_cases_equally() -> None:
    report = build_llf_human_agreement_report_from_path(AGREEMENT_PATH)
    case_rows = cast(list[dict[str, Any]], report["case_summaries"])
    macro = cast(dict[str, Any], report["case_macro_summary"])

    assert macro["denominator_cases"] == 20
    assert macro["available_pair_canonical_exact_match_rate"] == round(
        math.fsum(float(row["available_pair_canonical_exact_match_rate"]) for row in case_rows)
        / 20,
        12,
    )
    macro_metrics = cast(dict[str, dict[str, float]], macro["metrics"])
    for metric_name in ("nodes", "edges", "typed_components"):
        for score_name in ("precision", "recall", "f1"):
            expected = round(
                math.fsum(
                    float(cast(dict[str, Any], row["metrics"])[metric_name][score_name])
                    for row in case_rows
                )
                / 20,
                12,
            )
            assert macro_metrics[metric_name][score_name] == expected


def test_typed_records_reproduce_identically_in_any_input_order() -> None:
    records = load_llf_records(AGREEMENT_PATH)
    from_path = build_llf_human_agreement_report_from_path(AGREEMENT_PATH)
    reversed_in_memory = build_llf_human_agreement_report(reversed(records))

    assert reversed_in_memory == from_path
    assert _pretty_bytes(reversed_in_memory) == llf_human_agreement_report_bytes(AGREEMENT_PATH)


def test_typed_input_rejects_missing_duplicate_and_tampered_rows() -> None:
    records = list(load_llf_records(AGREEMENT_PATH))

    with pytest.raises(LlfAgreementDataError, match="exactly 60"):
        build_llf_human_agreement_report(records[:-1])

    duplicated = [*records[:-1], records[0]]
    with pytest.raises(LlfAgreementDataError, match="duplicate agreement annotation"):
        build_llf_human_agreement_report(duplicated)

    source_tampered = [
        records[0].model_copy(update={"raw_text_sha256": "f" * 64}),
        *records[1:],
    ]
    with pytest.raises(LlfAgreementDataError, match="criterion source bytes are tampered"):
        build_llf_human_agreement_report(source_tampered)

    logical_form_tampered = [
        records[0].model_copy(update={"logical_form": 'cond("tampered")'}),
        *records[1:],
    ]
    with pytest.raises(LlfAgreementDataError, match="logical-form bytes are tampered"):
        build_llf_human_agreement_report(logical_form_tampered)

    canonical_field_tampered = [
        records[0].model_copy(update={"source_file_bytes": records[0].source_file_bytes + 1}),
        *records[1:],
    ]
    with pytest.raises(LlfAgreementDataError, match="frozen hash"):
        build_llf_human_agreement_report(canonical_field_tampered)


def test_explicit_path_rejects_wrong_name_and_wrong_bytes(tmp_path: Path) -> None:
    payload = AGREEMENT_PATH.read_bytes()
    wrong_name = tmp_path / "copy.jsonl"
    wrong_name.write_bytes(payload)
    with pytest.raises(LlfAgreementDataError, match="must be named"):
        build_llf_human_agreement_report_from_path(wrong_name)

    agreement_dir = tmp_path / "tampered"
    agreement_dir.mkdir()
    tampered = agreement_dir / "agreement_annotations.jsonl"
    tampered.write_bytes(payload[:-1] + b" ")
    with pytest.raises(LlfAgreementDataError, match="frozen hash"):
        build_llf_human_agreement_report_from_path(tampered)


def test_parser_identity_binds_the_current_normalized_source() -> None:
    parser_path = Path(cast(str, llf_semantics.__file__))
    normalized = parser_path.read_text(encoding="utf-8").replace("\r\n", "\n")

    assert hashlib.sha256(normalized.encode()).hexdigest() == PINNED_PARSER_SOURCE_SHA256


def test_report_artifact_is_byte_stable_sealed_and_cli_checkable() -> None:
    expected = llf_human_agreement_report_bytes(AGREEMENT_PATH)
    assert REPORT_PATH.read_bytes() == expected
    assert main([str(AGREEMENT_PATH), "--output", str(REPORT_PATH), "--check"]) == 0

    report = cast(dict[str, Any], json.loads(expected))
    seal = cast(str, report.pop("canonical_payload_sha256"))
    assert seal == _payload_sha256(report)
    agreement_input = report["inputs"]["agreement_annotations"]
    assert agreement_input["sha256"] == PINNED_AGREEMENT_SHA256
    assert "not clinical validation" in " ".join(report["limitations"]).lower()


def test_report_internal_hashes_bind_each_deterministic_section() -> None:
    report = build_llf_human_agreement_report_from_path(AGREEMENT_PATH)
    hashes = cast(dict[str, str], report["hashes"])

    assert hashes == {
        "parsed_annotations_sha256": _payload_sha256(report["parsed_annotations"]),
        "available_pairs_sha256": _payload_sha256(report["available_pairs"]),
        "unavailable_pairs_sha256": _payload_sha256(report["unavailable_pairs"]),
        "case_summaries_sha256": _payload_sha256(report["case_summaries"]),
    }


def test_module_has_no_environment_provider_or_network_imports() -> None:
    source = Path(llf_agreement.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    imported_roots: set[str] = set()
    called_attributes: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported_roots.update(alias.name.partition(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            imported_roots.add(node.module.partition(".")[0])
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            called_attributes.add(node.func.attr)

    assert {
        "httpx",
        "openai",
        "os",
        "requests",
        "socket",
        "urllib",
    }.isdisjoint(imported_roots)
    assert {"environ", "getenv", "urlopen"}.isdisjoint(called_attributes)
