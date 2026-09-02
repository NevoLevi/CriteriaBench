from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

import criteriabench.real.llf_semantics as llf_semantics
from criteriabench.real.graph_v2 import strict_output_schema
from criteriabench.real.llf import EXPECTED_MISSING_UPSTREAM_CASE_IDS
from criteriabench.real.llf_semantics import (
    LlfCallNode,
    LlfMatchCounts,
    LlfSemanticOutput,
    LlfSemanticReference,
    compare_llf_semantics,
    failed_llf_semantic_comparison,
    inflate_llf_semantic_output,
    llf_model_input,
    llf_semantic_components,
    llf_semantic_strict_json_schema,
    load_llf_generation_cases,
    load_llf_scoring_references,
    parse_llf_semantic,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DATA_ROOT = PROJECT_ROOT / "data" / "real" / "llf"
COVERAGE_PATHS = {
    "development": PROJECT_ROOT / "docs" / "results" / "llf-semantic-coverage-development.json",
    "test": PROJECT_ROOT / "docs" / "results" / "llf-semantic-coverage-test.json",
}


def _output(source: str) -> LlfSemanticOutput:
    parsed = parse_llf_semantic(source)
    return LlfSemanticOutput(root_node_id=parsed.root_node_id, nodes=parsed.nodes)


def test_provider_output_is_identity_free_flat_and_strict_schema_compatible() -> None:
    raw_schema = LlfSemanticOutput.model_json_schema()
    schema = llf_semantic_strict_json_schema()
    serialized = json.dumps(schema, sort_keys=True, separators=(",", ":"))

    assert schema == strict_output_schema(raw_schema)
    assert schema["required"] == ["schema_version", "root_node_id", "nodes"]
    assert schema["additionalProperties"] is False
    assert "case_id" not in serialized
    assert "trial_id" not in serialized
    assert "source_sha256" not in serialized
    assert "reference_sha256" not in serialized
    assert "logical_form" not in serialized
    assert hashlib.sha256(serialized.encode()).hexdigest() == (
        "9fcedf580e314c774c085347ed98c55a81f3121f876664912769e54872a2d555"
    )

    stack: list[object] = [schema]
    while stack:
        value = stack.pop()
        if isinstance(value, dict):
            if value.get("type") == "object":
                assert value["additionalProperties"] is False
                assert value["required"] == list(value["properties"])
            stack.extend(value.values())
        elif isinstance(value, list):
            stack.extend(value)


def test_output_rejects_identity_injection_and_shared_ast_nodes() -> None:
    valid = _output('cond("x")')
    payload = valid.model_dump(mode="json")
    payload["case_id"] = "NCT00000000_0"
    with pytest.raises(ValidationError):
        LlfSemanticOutput.model_validate(payload)

    cond = parse_llf_semantic('cond("x")')
    union_symbol = cond.nodes[0].model_copy(update={"node_id": "n0003", "name": "union"})
    shared_call = LlfCallNode(
        node_id="n0004",
        callee_node_id="n0003",
        argument_node_ids=("n0002", "n0002"),
    )
    with pytest.raises(ValidationError, match="without shared child"):
        LlfSemanticOutput(
            root_node_id="n0004",
            nodes=(*cond.nodes, union_symbol, shared_call),
        )


def test_trusted_source_identity_is_inflated_outside_model_output() -> None:
    output = _output('age().num_filter(eq(op(GTEQ), val("18")))')

    reference = inflate_llf_semantic_output(
        output,
        trusted_source_sha256="a" * 64,
    )

    assert reference.source_sha256 == "a" * 64
    assert reference.root_node_id == output.root_node_id
    assert reference.nodes == output.nodes
    with pytest.raises(ValidationError):
        inflate_llf_semantic_output(output, trusted_source_sha256="not-a-hash")


@pytest.mark.parametrize("operator", ["and", "intersect", "or", "union"])
def test_only_known_commutative_calls_are_reorder_equivalent(operator: str) -> None:
    reference = _output(f'{operator}(cond("a"), cond("b"))')
    prediction = _output(f'{operator}(cond("b"), cond("a"))')

    comparison = compare_llf_semantics(prediction, reference)

    assert comparison.exact_match is True
    assert comparison.structure.false_positive == 0
    assert comparison.structure.false_negative == 0
    assert comparison.structure.precision == 1.0
    assert comparison.structure.recall == 1.0
    assert comparison.structure.f1 == 1.0


def test_infix_boolean_operations_are_commutative_but_seq_remains_ordered() -> None:
    infix_reference = _output('cond("a") or cond("b")')
    infix_prediction = _output('cond("b") or cond("a")')
    seq_reference = _output('seq(cond("a"), cond("b"))')
    seq_prediction = _output('seq(cond("b"), cond("a"))')

    assert compare_llf_semantics(infix_prediction, infix_reference).exact_match is True
    ordered = compare_llf_semantics(seq_prediction, seq_reference)
    assert ordered.exact_match is False
    assert ordered.edges.false_positive == 2
    assert ordered.edges.false_negative == 2
    assert ordered.nodes == LlfMatchCounts(8, 0, 0)


def test_undocumented_method_form_union_remains_strictly_ordered() -> None:
    reference = _output('cond("receiver").union(cond("a"), cond("b"))')
    reordered_arguments = _output('cond("receiver").union(cond("b"), cond("a"))')

    comparison = compare_llf_semantics(reordered_arguments, reference)
    assert comparison.exact_match is False
    assert comparison.edges.false_positive > 0
    assert comparison.edges.false_negative > 0


def test_literal_and_method_attribute_errors_receive_typed_partial_credit() -> None:
    reference = _output('cond("A").mod("x")')
    prediction = _output('cond("B").pol("x")')

    comparison = compare_llf_semantics(prediction, reference)

    assert comparison.exact_match is False
    assert comparison.calls == LlfMatchCounts(1, 1, 1)
    assert comparison.method_attributes == LlfMatchCounts(0, 1, 1)
    assert comparison.symbols == LlfMatchCounts(1, 0, 0)
    assert comparison.strings == LlfMatchCounts(1, 1, 1)
    assert comparison.strings.precision == 0.5
    assert comparison.strings.recall == 0.5
    assert comparison.strings.f1 == 0.5
    assert comparison.typed_components == LlfMatchCounts(3, 3, 3)

    boolean_error = compare_llf_semantics(
        _output("proc().for(False)"),
        _output("proc().for(True)"),
    )
    assert boolean_error.booleans == LlfMatchCounts(0, 1, 1)


def test_operational_failure_scores_zero_against_every_reference_component() -> None:
    reference = _output('intersect(cond("A"), proc().for(True))')
    expected = llf_semantic_components(reference)

    failure = failed_llf_semantic_comparison(reference)

    assert failure.exact_match is False
    for metric_name, component_name in (
        ("nodes", "nodes"),
        ("edges", "edges"),
        ("calls", "calls"),
        ("method_attributes", "method_attributes"),
        ("symbols", "symbols"),
        ("strings", "strings"),
        ("booleans", "booleans"),
    ):
        metric = getattr(failure, metric_name)
        component = getattr(expected, component_name)
        assert metric == LlfMatchCounts(0, 0, sum(component.values()))
        assert metric.precision == 0.0
        assert metric.recall == 0.0
        assert metric.f1 == 0.0


def test_generation_loader_uses_only_the_physical_source_only_artifact(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    accessed_paths: list[Path] = []
    original_read_bytes = Path.read_bytes

    def observed_read_bytes(path: Path) -> bytes:
        accessed_paths.append(path.resolve())
        if path.name in {
            "manifest.json",
            "records.jsonl",
            "development_references.jsonl",
            "test_references.jsonl",
        }:
            raise AssertionError("generation must never open reference-bearing artifacts")
        return original_read_bytes(path)

    monkeypatch.setattr(Path, "read_bytes", observed_read_bytes)
    cases = load_llf_generation_cases(
        DATA_ROOT / "generation_cases.jsonl",
        split="all",
    )

    assert len(cases) == 2_000
    assert len({case.case_id for case in cases}) == 2_000
    assert set(cases[0].model_dump()) == {
        "case_id",
        "trial_id",
        "split",
        "polarity",
        "source_text",
        "source_sha256",
    }
    forbidden = {"logical_form", "reference", "reference_sha256", "augmented_text"}
    assert forbidden.isdisjoint(LlfSemanticOutput.model_fields)
    assert all(forbidden.isdisjoint(type(case).model_fields) for case in cases)
    provider_input = llf_model_input(cases[0])
    assert set(provider_input.model_dump()) == {"polarity", "source_text"}
    assert "NCT" not in json.dumps(provider_input.model_dump())
    assert {path.name for path in accessed_paths} == {
        "generation_cases.jsonl",
        "generation_manifest.json",
    }


def test_reference_loader_separates_operational_and_semantic_denominators() -> None:
    development = load_llf_scoring_references(
        DATA_ROOT / "development_references.jsonl",
        COVERAGE_PATHS["development"],
        split="development",
    )
    test = load_llf_scoring_references(
        DATA_ROOT / "test_references.jsonl",
        COVERAGE_PATHS["test"],
        split="test",
    )

    assert development.operational_case_count + test.operational_case_count == 2_000
    assert development.semantic_case_count + test.semantic_case_count == 1_997
    assert len(development.references) + len(test.references) == 1_997
    assert not development.missing_upstream_case_ids
    assert set(test.missing_upstream_case_ids) == EXPECTED_MISSING_UPSTREAM_CASE_IDS
    assert not set(test.missing_upstream_case_ids).intersection(
        reference.case_id for reference in test.references
    )
    assert all(
        reference.reference_sha256 == reference.reference.source_sha256
        and len(reference.source_sha256) == 64
        for reference in (*development.references, *test.references)
    )


def test_reference_loader_can_freeze_the_locked_test_denominator_directly() -> None:
    corpus = load_llf_scoring_references(
        DATA_ROOT / "test_references.jsonl",
        COVERAGE_PATHS["test"],
        split="test",
    )

    assert corpus.split == "test"
    assert corpus.operational_case_count == 1_800
    assert corpus.semantic_case_count == 1_797
    assert len(corpus.missing_upstream_case_ids) == 3
    assert all(reference.split == "test" for reference in corpus.references)


def test_development_scoring_never_parses_a_locked_test_reference(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    parsed_source_names: list[str] = []
    accessed_paths: list[Path] = []
    original_parse = llf_semantics.parse_llf_semantic
    original_read_bytes = Path.read_bytes

    def observed_parse(
        source: str,
        *,
        source_name: str = "<memory>",
    ) -> LlfSemanticReference:
        parsed_source_names.append(source_name)
        return original_parse(source, source_name=source_name)

    monkeypatch.setattr(llf_semantics, "parse_llf_semantic", observed_parse)

    def observed_read_bytes(path: Path) -> bytes:
        accessed_paths.append(path.resolve())
        if path.name in {
            "manifest.json",
            "records.jsonl",
            "test_references.jsonl",
            "llf-semantic-coverage.json",
            "llf-semantic-coverage-test.json",
        }:
            raise AssertionError("development scoring crossed the physical split boundary")
        return original_read_bytes(path)

    monkeypatch.setattr(Path, "read_bytes", observed_read_bytes)
    corpus = load_llf_scoring_references(
        DATA_ROOT / "development_references.jsonl",
        COVERAGE_PATHS["development"],
        split="development",
    )

    called_case_ids = {Path(source_name).stem for source_name in parsed_source_names}
    assert len(parsed_source_names) == 200
    assert called_case_ids == {reference.case_id for reference in corpus.references}
    assert all(reference.split == "development" for reference in corpus.references)
    assert corpus.operational_case_count == corpus.semantic_case_count == 200
    assert {path.name for path in accessed_paths} == {
        "development_references.jsonl",
        "llf-semantic-coverage-development.json",
    }


def test_generation_loader_rejects_a_tampered_source_only_artifact(
    tmp_path: Path,
) -> None:
    generation = (DATA_ROOT / "generation_cases.jsonl").read_bytes()
    tampered_generation = tmp_path / "generation_cases.jsonl"
    tampered_generation.write_bytes(generation.replace(b'"test"', b'"development"', 1))
    (tmp_path / "generation_manifest.json").write_bytes(
        (DATA_ROOT / "generation_manifest.json").read_bytes()
    )

    with pytest.raises(ValueError, match="artifact does not match"):
        load_llf_generation_cases(tampered_generation, split="all")


def test_reference_loader_rejects_a_coverage_artifact_with_a_broken_seal(
    tmp_path: Path,
) -> None:
    coverage = json.loads(COVERAGE_PATHS["development"].read_bytes())
    coverage["coverage"]["parsed_references"] = 199
    tampered = tmp_path / "coverage.json"
    tampered.write_text(json.dumps(coverage), encoding="utf-8")

    with pytest.raises(ValueError, match="frozen pin"):
        load_llf_scoring_references(
            DATA_ROOT / "development_references.jsonl",
            tampered,
            split="development",
        )
