from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, cast

import pytest
from pydantic import ValidationError

from criteriabench.real.llf import EXPECTED_MISSING_UPSTREAM_CASE_IDS, load_llf_records
from criteriabench.real.llf_semantics import (
    LlfAttributeNode,
    LlfBooleanNode,
    LlfBooleanOperationNode,
    LlfCallNode,
    LlfSemanticParseError,
    LlfSemanticReference,
    LlfStringNode,
    LlfSymbolNode,
    LlfTupleNode,
    build_semantic_coverage_report,
    canonical_llf_json,
    canonical_llf_sha256,
    main,
    parse_llf_semantic,
    render_llf_semantic,
    semantic_coverage_report_bytes,
    semantic_tree_sha256,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DATA_ROOT = PROJECT_ROOT / "data" / "real" / "llf"
COVERAGE_ARTIFACT = PROJECT_ROOT / "docs" / "results" / "llf-semantic-coverage.json"
DEVELOPMENT_COVERAGE_ARTIFACT = (
    PROJECT_ROOT / "docs" / "results" / "llf-semantic-coverage-development.json"
)


def test_method_chain_is_preserved_as_an_ordered_flat_node_table() -> None:
    source = 'cond("HIV").pol(POSITIVE).mod("confirmed")'

    reference = parse_llf_semantic(source)

    assert reference.source_sha256 == hashlib.sha256(source.encode()).hexdigest()
    assert [node.node_id for node in reference.nodes] == [
        f"n{index:04d}" for index in range(len(reference.nodes))
    ]
    assert [node.kind for node in reference.nodes] == [
        "symbol",
        "string",
        "call",
        "attribute",
        "symbol",
        "call",
        "attribute",
        "string",
        "call",
    ]
    first_call = reference.nodes[2]
    assert isinstance(first_call, LlfCallNode)
    assert first_call.argument_node_ids == ("n0001",)
    final_call = reference.nodes[-1]
    assert isinstance(final_call, LlfCallNode)
    assert final_call.callee_node_id == "n0006"
    assert final_call.argument_node_ids == ("n0007",)


def test_keyword_identifiers_are_normalized_only_outside_strings() -> None:
    source = 'or(cond("and or .for .except"), proc().for(True), proc().except(False))'

    reference = parse_llf_semantic(source)

    symbols = [node.name for node in reference.nodes if isinstance(node, LlfSymbolNode)]
    attributes = [node.attribute for node in reference.nodes if isinstance(node, LlfAttributeNode)]
    strings = [node.value for node in reference.nodes if isinstance(node, LlfStringNode)]
    booleans = [node.value for node in reference.nodes if isinstance(node, LlfBooleanNode)]
    assert "or" in symbols
    assert attributes == ["for", "except"]
    assert strings == ["and or .for .except"]
    assert booleans == [True, False]


def test_infix_boolean_operation_remains_distinct_from_or_function_call() -> None:
    infix = parse_llf_semantic('eq(op(LT), val("18")) or eq(op(GT), val("60"))')
    function = parse_llf_semantic('or(eq(op(LT), val("18")), eq(op(GT), val("60")))')

    infix_root = infix.nodes[-1]
    function_root = function.nodes[-1]
    assert isinstance(infix_root, LlfBooleanOperationNode)
    assert infix_root.operator == "or"
    assert isinstance(function_root, LlfCallNode)
    function_callee = function.nodes[int(function_root.callee_node_id[1:])]
    assert isinstance(function_callee, LlfSymbolNode)
    assert function_callee.name == "or"
    assert semantic_tree_sha256(infix) != semantic_tree_sha256(function)


def test_tuple_and_nested_call_are_preserved_without_repairing_upstream() -> None:
    tuple_reference = parse_llf_semantic('cond("a"), union(cond("b"), cond("c"))')
    nested_call = parse_llf_semantic('age().num_filter()(eq(op(GT), val("18")))')

    assert isinstance(tuple_reference.nodes[-1], LlfTupleNode)
    assert tuple_reference.nodes[-1].item_node_ids == ("n0002", "n0010")
    assert isinstance(nested_call.nodes[-1], LlfCallNode)
    nested_callee = nested_call.nodes[int(nested_call.nodes[-1].callee_node_id[1:])]
    assert isinstance(nested_callee, LlfCallNode)


def test_canonical_render_round_trips_structure_but_not_source_formatting() -> None:
    compact = parse_llf_semantic('cond("café\\nvalue").mod("x")')
    spaced = parse_llf_semantic(
        """
        cond(
            "café\\nvalue"
        )
        .mod("x")
        """
    )

    assert semantic_tree_sha256(compact) == semantic_tree_sha256(spaced)
    assert canonical_llf_sha256(compact) != canonical_llf_sha256(spaced)
    rendered = render_llf_semantic(compact)
    reparsed = parse_llf_semantic(rendered)
    assert semantic_tree_sha256(reparsed) == semantic_tree_sha256(compact)
    assert canonical_llf_json(compact).endswith("\n")


@pytest.mark.parametrize(
    "source",
    [
        '__import__("os").system("calc")',
        'safe().__class__("x")',
        'os.system("calc")',
        'safe().system("calc")',
        'eval("cond()")',
        'open("file")',
    ],
)
def test_dangerous_identifiers_and_attributes_are_rejected(source: str) -> None:
    with pytest.raises(LlfSemanticParseError) as caught:
        parse_llf_semantic(source)

    assert caught.value.code == "unsafe_identifier"


@pytest.mark.parametrize(
    ("source", "error_code"),
    [
        ('cond("x") # hidden', "comments_not_allowed"),
        ('cond(f"x")', "prefixed_string_not_allowed"),
        ('cond("x" "y")', "implicit_string_concatenation"),
        ("cond(123)", "disallowed_token"),
        ('cond(["x"])', "disallowed_token"),
        ('cond(value="x")', "disallowed_token"),
        ('cond("x"); proc("y")', "disallowed_token"),
        ('cond("x")\nproc("y")', "malformed_expression"),
    ],
)
def test_non_llf_syntax_is_rejected_with_stable_codes(
    source: str,
    error_code: str,
) -> None:
    with pytest.raises(LlfSemanticParseError) as caught:
        parse_llf_semantic(source)

    assert caught.value.code == error_code


def test_flat_contract_rejects_forward_references_and_extra_fields() -> None:
    with pytest.raises(ValidationError, match="earlier node"):
        LlfSemanticReference(
            source_sha256="a" * 64,
            root_node_id="n0001",
            nodes=(
                LlfSymbolNode(node_id="n0000", name="cond"),
                LlfCallNode(
                    node_id="n0001",
                    callee_node_id="n0002",
                    argument_node_ids=(),
                ),
            ),
        )

    with pytest.raises(ValidationError):
        LlfSymbolNode.model_validate(
            {"node_id": "n0000", "kind": "symbol", "name": "cond", "unexpected": True}
        )


def test_all_parseable_committed_references_round_trip_canonically() -> None:
    rows = (
        *load_llf_records(DATA_ROOT / "records.jsonl"),
        *load_llf_records(DATA_ROOT / "agreement_annotations.jsonl"),
    )
    parsed = 0
    malformed = 0
    for row in rows:
        if row.logical_form is None:
            continue
        try:
            reference = parse_llf_semantic(row.logical_form, source_name=row.source_path)
        except LlfSemanticParseError:
            malformed += 1
            continue
        assert reference.source_sha256 == row.reference_sha256
        reparsed = parse_llf_semantic(render_llf_semantic(reference))
        assert semantic_tree_sha256(reparsed) == semantic_tree_sha256(reference)
        parsed += 1

    assert parsed == 2_054
    assert malformed == 3


def test_committed_coverage_is_complete_explicit_and_byte_reproducible() -> None:
    records_path = DATA_ROOT / "records.jsonl"
    agreement_path = DATA_ROOT / "agreement_annotations.jsonl"
    report = build_semantic_coverage_report(records_path, agreement_path)
    coverage = cast(dict[str, dict[str, Any]], report["coverage"])
    primary = coverage["primary"]
    agreement = coverage["agreement"]

    assert primary["total_rows"] == 2_000
    assert primary["available_references"] == 1_997
    assert primary["parsed_references"] == 1_997
    assert primary["malformed_references"] == 0
    assert primary["missing_upstream_references"] == 3
    assert {row["case_id"] for row in primary["missing"]} == (EXPECTED_MISSING_UPSTREAM_CASE_IDS)
    assert agreement["total_rows"] == 60
    assert agreement["parsed_references"] == 57
    assert agreement["malformed_references"] == 3
    assert {(row["case_id"], row["annotator_id"]) for row in agreement["malformed"]} == {
        ("NCT03861962_2", "annotator_3"),
        ("NCT03862937_2", "annotator_2"),
        ("NCT03927456_8", "annotator_3"),
    }

    vocabulary = cast(dict[str, Any], report["vocabulary"])
    assert vocabulary["node_kind_counts"]["tuple"] == 1
    assert vocabulary["node_kind_counts"]["boolean_operation"] == 1
    assert vocabulary["indirect_call_callee_kind_counts"] == {"call": 3}
    assert {"and", "or", "intersect", "union"} <= set(vocabulary["direct_call_names"])
    assert {"except", "for", "num_filter", "temporality"} <= set(vocabulary["method_call_names"])

    payload = semantic_coverage_report_bytes(records_path, agreement_path)
    assert payload == COVERAGE_ARTIFACT.read_bytes()
    artifact = json.loads(payload)
    seal = artifact.pop("canonical_payload_sha256")
    canonical_payload = (
        json.dumps(artifact, ensure_ascii=False, separators=(",", ":"), sort_keys=True) + "\n"
    ).encode()
    assert hashlib.sha256(canonical_payload).hexdigest() == seal


def test_coverage_cli_writes_canonical_lf_bytes(
    capfdbinary: pytest.CaptureFixture[bytes],
) -> None:
    assert (
        main(
            [
                "--split-reference",
                str(DATA_ROOT / "development_references.jsonl"),
                "--split",
                "development",
            ]
        )
        == 0
    )
    captured = capfdbinary.readouterr()
    assert captured.out == DEVELOPMENT_COVERAGE_ARTIFACT.read_bytes()
    assert b"\r" not in captured.out
