from __future__ import annotations

import ast
import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, Literal

import pytest

import criteriabench.real.llf_baselines as llf_baselines
from criteriabench.real.llf_baselines import (
    BASELINE_ALGORITHM_CONTRACT_SHA256,
    BASELINE_CODE_SHA256,
    BASELINE_CONFIGURATION_SHA256,
    BASELINE_IDENTITY_SHA256,
    FROZEN_BASELINE_IDENTITY,
    LlfBaselineDataError,
    LlfBaselineUnavailableError,
    build_llf_bm25_baseline,
    unicode_tokens,
)
from criteriabench.real.llf_semantics import (
    LlfGenerationCase,
    LlfScoringReference,
    LlfSemanticOutput,
    parse_llf_semantic,
)


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _case(
    trial_number: int,
    criterion_index: int,
    text: str,
    *,
    split: Literal["development", "test"] = "development",
    polarity: Literal["inclusion", "exclusion"] = "inclusion",
) -> LlfGenerationCase:
    trial_id = f"NCT{trial_number:08d}"
    return LlfGenerationCase(
        case_id=f"{trial_id}_{criterion_index}",
        trial_id=trial_id,
        split=split,
        polarity=polarity,
        source_text=text,
        source_sha256=_sha256(text),
    )


def _reference(
    case: LlfGenerationCase,
    logical_form: str,
    *,
    source_sha256: str | None = None,
    trial_id: str | None = None,
    split: Literal["development", "test"] = "development",
) -> LlfScoringReference:
    semantic = parse_llf_semantic(logical_form, source_name=f"fixture:{case.case_id}")
    return LlfScoringReference(
        case_id=case.case_id,
        trial_id=trial_id or case.trial_id,
        split=split,
        source_sha256=source_sha256 or case.source_sha256,
        reference_sha256=semantic.source_sha256,
        reference=semantic,
    )


def _output(logical_form: str) -> LlfSemanticOutput:
    semantic = parse_llf_semantic(logical_form, source_name="expected")
    return LlfSemanticOutput(root_node_id=semantic.root_node_id, nodes=semantic.nodes)


def _all_keys(value: object) -> set[str]:
    if isinstance(value, Mapping):
        return {str(key) for key in value} | {
            nested_key for nested in value.values() for nested_key in _all_keys(nested)
        }
    if isinstance(value, Sequence) and not isinstance(value, str | bytes):
        return {nested_key for nested in value for nested_key in _all_keys(nested)}
    return set()


def test_frozen_algorithm_identity_and_hashes_are_regression_pinned() -> None:
    assert BASELINE_CONFIGURATION_SHA256 == (
        "69564a6fff8084acc74b2434435eea0fe23beeca0472401620f37440bb56d556"
    )
    assert BASELINE_ALGORITHM_CONTRACT_SHA256 == (
        "45903d14b986782e2cf2e6d06e552e0abec7f9a8a42ffa6c4dbe4455004a918a"
    )
    assert BASELINE_CODE_SHA256 == (
        "e2626cb7aeb8b7117e4f4aacf5bba583565b787faef2bde84117361300fa7b1d"
    )
    assert BASELINE_IDENTITY_SHA256 == (
        "1516cd81a29b653df282f8d44e458b80f052215cbd35ef477be9e277e4dc047f"
    )
    identity = FROZEN_BASELINE_IDENTITY.as_dict()
    identity["baseline_id"] = "mutated-copy"
    assert FROZEN_BASELINE_IDENTITY.baseline_id == "llf-bm25-nearest-development-v1"


def test_code_hash_binds_normalized_implementation_source() -> None:
    module_path = Path(llf_baselines.__file__)
    source = module_path.read_text(encoding="utf-8").replace("\r\n", "\n")
    normalized, replacement_count = re.subn(
        r'(?m)^(BASELINE_CODE_SHA256: Final = ")[0-9a-f]{64}(")$',
        lambda match: f"{match.group(1)}{'0' * 64}{match.group(2)}",
        source,
    )

    assert replacement_count == 1
    assert hashlib.sha256(normalized.encode("utf-8")).hexdigest() == BASELINE_CODE_SHA256


def test_module_has_no_file_environment_provider_or_network_capability() -> None:
    source = Path(llf_baselines.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    imported_roots: set[str] = set()
    called_names: set[str] = set()
    called_attributes: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported_roots.update(alias.name.partition(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            imported_roots.add(node.module.partition(".")[0])
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            called_names.add(node.func.id)
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            called_attributes.add(node.func.attr)

    assert {
        "httpx",
        "openai",
        "os",
        "pathlib",
        "requests",
        "socket",
        "urllib",
    }.isdisjoint(imported_roots)
    assert "open" not in called_names
    assert {"getenv", "read_bytes", "read_text", "urlopen"}.isdisjoint(called_attributes)


def test_unicode_tokenization_is_nfkc_casefolded_and_language_agnostic() -> None:
    assert unicode_tokens("CAFÉ Straße — 患者, β-blocker; A_B e\u0301") == (
        "café",
        "strasse",
        "患者",
        "β",
        "blocker",
        "a",
        "b",
        "é",
    )


def test_nearest_clinical_text_copies_the_sensible_development_ast() -> None:
    asthma = _case(1, 0, "Severe asthma requiring inhaled corticosteroids")
    diabetes = _case(2, 0, "Type 2 diabetes treated with metformin")
    baseline = build_llf_bm25_baseline(
        [diabetes, asthma],
        [_reference(asthma, 'cond("asthma")'), _reference(diabetes, 'cond("diabetes")')],
    )
    target = _case(
        100,
        0,
        "Patients with severe asthma using inhaled corticosteroids",
        split="test",
    )

    assert baseline.predict(target) == _output('cond("asthma")')
    assert baseline.training_case_count == 2
    assert baseline.training_trial_count == 2


def test_development_prediction_excludes_the_entire_target_trial() -> None:
    exact = _case(10, 0, "chronic kidney disease exact phrase")
    same_trial = _case(10, 1, "kidney disease exact phrase")
    other_trial = _case(11, 0, "kidney disease eligibility")
    baseline = build_llf_bm25_baseline(
        [exact, same_trial, other_trial],
        [
            _reference(exact, 'cond("leaked-exact")'),
            _reference(same_trial, 'cond("leaked-sibling")'),
            _reference(other_trial, 'cond("safe-other-trial")'),
        ],
    )

    assert baseline.predict(exact) == _output('cond("safe-other-trial")')

    locked_target = _case(
        10,
        99,
        exact.source_text,
        split="test",
    )
    assert baseline.predict(locked_target) == _output('cond("leaked-exact")')


def test_ties_and_training_hash_are_deterministic_across_input_order() -> None:
    lower_id = _case(20, 0, "adult participant")
    higher_id = _case(21, 0, "adult participant")
    lower_reference = _reference(lower_id, 'cond("lower-id")')
    higher_reference = _reference(higher_id, 'cond("higher-id")')
    forward = build_llf_bm25_baseline(
        [lower_id, higher_id],
        [lower_reference, higher_reference],
    )
    reversed_order = build_llf_bm25_baseline(
        [higher_id, lower_id],
        [higher_reference, lower_reference],
    )
    target = _case(200, 0, "adult participant", split="test")

    assert forward.predict(target) == _output('cond("lower-id")')
    assert reversed_order.predict(target) == forward.predict(target)
    assert reversed_order.training_set_sha256 == forward.training_set_sha256


def test_join_rejects_duplicates_missing_rows_and_provenance_mismatches() -> None:
    first = _case(30, 0, "first criterion")
    second = _case(31, 0, "second criterion")
    first_reference = _reference(first, 'cond("first")')

    with pytest.raises(LlfBaselineDataError, match="duplicate development case"):
        build_llf_bm25_baseline([first, first], [first_reference])
    with pytest.raises(LlfBaselineDataError, match="duplicate development reference"):
        build_llf_bm25_baseline([first], [first_reference, first_reference])
    with pytest.raises(LlfBaselineDataError, match="do not join one-to-one"):
        build_llf_bm25_baseline([first, second], [first_reference])

    wrong_hash_reference = _reference(first, 'cond("first")', source_sha256="f" * 64)
    with pytest.raises(LlfBaselineDataError, match="criterion source hash mismatch"):
        build_llf_bm25_baseline([first], [wrong_hash_reference])

    wrong_trial_reference = _reference(first, 'cond("first")', trial_id="NCT99999999")
    with pytest.raises(LlfBaselineDataError, match="trial identity mismatch"):
        build_llf_bm25_baseline([first], [wrong_trial_reference])

    tampered_case = first.model_copy(update={"source_sha256": "f" * 64})
    with pytest.raises(LlfBaselineDataError, match="source bytes are tampered"):
        build_llf_bm25_baseline([tampered_case], [wrong_hash_reference])

    tampered_reference = first_reference.model_copy(update={"reference_sha256": "f" * 64})
    with pytest.raises(LlfBaselineDataError, match="reference lineage is tampered"):
        build_llf_bm25_baseline([first], [tampered_reference])


def test_join_rejects_non_development_training_inputs() -> None:
    development = _case(40, 0, "development")
    test_case = _case(41, 0, "test", split="test")

    with pytest.raises(LlfBaselineDataError, match="cases must all be"):
        build_llf_bm25_baseline([test_case], [_reference(test_case, 'cond("x")')])
    with pytest.raises(LlfBaselineDataError, match="references must all be"):
        build_llf_bm25_baseline(
            [development],
            [_reference(development, 'cond("x")', split="test")],
        )


def test_prediction_is_identity_free_and_does_not_alias_reference_type() -> None:
    first = _case(50, 0, "heart failure")
    second = _case(51, 0, "diabetes mellitus")
    baseline = build_llf_bm25_baseline(
        [first, second],
        [_reference(first, 'cond("heart-failure")'), _reference(second, 'cond("diabetes")')],
    )
    output = baseline.predict(_case(500, 0, "heart failure", split="test"))
    document: dict[str, Any] = output.model_dump(mode="json")

    assert type(output) is LlfSemanticOutput
    assert set(document) == {"schema_version", "root_node_id", "nodes"}
    assert {
        "case_id",
        "trial_id",
        "split",
        "polarity",
        "source_text",
        "source_sha256",
        "reference_sha256",
        "reference",
    }.isdisjoint(_all_keys(document))
    serialized = json.dumps(document, sort_keys=True)
    assert first.case_id not in serialized
    assert first.source_sha256 not in serialized


def test_prediction_fails_closed_when_trial_or_polarity_leaves_no_neighbor() -> None:
    only = _case(60, 0, "only development example")
    baseline = build_llf_bm25_baseline([only], [_reference(only, 'cond("only")')])

    with pytest.raises(LlfBaselineUnavailableError, match="leave-one-trial-out"):
        baseline.predict(only)
    with pytest.raises(LlfBaselineUnavailableError, match="same-polarity"):
        baseline.predict(_case(600, 0, "an exclusion", split="test", polarity="exclusion"))


def test_prediction_rechecks_target_source_bytes() -> None:
    first = _case(70, 0, "first")
    second = _case(71, 0, "second")
    baseline = build_llf_bm25_baseline(
        [first, second],
        [_reference(first, 'cond("first")'), _reference(second, 'cond("second")')],
    )
    target = _case(700, 0, "first", split="test").model_copy(update={"source_sha256": "f" * 64})

    with pytest.raises(LlfBaselineDataError, match="target criterion source bytes are tampered"):
        baseline.predict(target)
