from __future__ import annotations

import hashlib
import json
from copy import deepcopy

import pytest
from pydantic import ValidationError

from criteriabench.real import (
    MAX_FLAT_GRAPH_DEPTH,
    AllOf,
    AnyOf,
    AtLeast,
    Comparator,
    Concept,
    CriterionKindV2,
    EligibilityGraphV2,
    EvidenceSpanV2,
    EvidenceValidationError,
    FlatAllOfNodeV2,
    FlatGraphOutputV2,
    FlatNotNodeV2,
    FlatPredicateNodeV2,
    Modifier,
    ModifierKind,
    Not,
    Predicate,
    RangeValue,
    ScalarType,
    ScalarValue,
    SetValue,
    SourceDocument,
    TemporalConstraintV2,
    TemporalRelationV2,
    Unit,
    canonical_graph_json,
    canonical_graph_sha256,
    canonicalize_graph,
    flat_graph_strict_json_schema,
    inflate_model_output,
    iter_expressions,
    strict_output_schema,
    validate_evidence,
)

SOURCE_TEXT = "Age 18 years; diabetes; kidney disease"


def _span(quote: str) -> EvidenceSpanV2:
    start = SOURCE_TEXT.index(quote)
    return EvidenceSpanV2(start_char=start, end_char=start + len(quote), quote=quote)


def _predicate(quote: str) -> Predicate:
    return Predicate(
        kind="predicate",
        concept=Concept(text=quote),
        comparator=Comparator.UNSPECIFIED,
        evidence=(_span(quote),),
    )


def _graph(root: object) -> EligibilityGraphV2:
    return EligibilityGraphV2(
        schema_version="2.0",
        source=SourceDocument.from_text(
            trial_id="NCT00000001",
            document_id="NCT00000001-I-000",
            text=SOURCE_TEXT,
        ),
        criterion_id="NCT00000001-I-000",
        criterion_kind=CriterionKindV2.INCLUSION,
        root=root,
        review_required=False,
        review_reasons=(),
        not_machine_executable=False,
    )


def _flat_predicate(node_id: str, quote: str) -> FlatPredicateNodeV2:
    return FlatPredicateNodeV2(
        node_id=node_id,
        kind="predicate",
        concept=Concept(text=quote),
        comparator=Comparator.UNSPECIFIED,
        value=None,
        unit=None,
        temporal=(),
        modifiers=(),
        evidence=(_span(quote),),
    )


def _flat_output(
    *,
    root_node_id: str | None,
    nodes: tuple[object, ...],
    review_required: bool = False,
    review_reasons: tuple[str, ...] = (),
    not_machine_executable: bool = False,
) -> FlatGraphOutputV2:
    return FlatGraphOutputV2(
        schema_version="2.0",
        root_node_id=root_node_id,
        nodes=nodes,
        review_required=review_required,
        review_reasons=review_reasons,
        not_machine_executable=not_machine_executable,
    )


def test_source_identity_uses_unicode_character_offsets_and_utf8_hash() -> None:
    text = "🙂 Adult"
    source = SourceDocument.from_text(
        trial_id="NCT-UNICODE",
        document_id="unicode-criterion",
        text=text,
    )

    assert source.text_length == 7
    assert len(source.text_sha256) == 64


def test_evidence_span_requires_half_open_length_to_equal_quote() -> None:
    with pytest.raises(ValidationError, match="span length"):
        EvidenceSpanV2(start_char=0, end_char=4, quote="Adult")


def test_graph_rejects_evidence_beyond_declared_source_bounds() -> None:
    source = SourceDocument.from_text(
        trial_id="NCT-BOUNDS",
        document_id="bounds-criterion",
        text="Adult",
    )
    predicate = Predicate(
        kind="predicate",
        concept=Concept(text="Adult"),
        comparator=Comparator.UNSPECIFIED,
        evidence=(EvidenceSpanV2(start_char=5, end_char=10, quote="Adult"),),
    )

    with pytest.raises(ValidationError, match="exceeds source"):
        EligibilityGraphV2(
            schema_version="2.0",
            source=source,
            criterion_id="bounds-criterion",
            criterion_kind="inclusion",
            root=predicate,
            review_required=False,
            review_reasons=(),
            not_machine_executable=False,
        )


def test_validate_evidence_checks_source_identity_then_exact_quotes() -> None:
    graph = _graph(_predicate("diabetes"))
    validate_evidence(graph, SOURCE_TEXT)

    with pytest.raises(EvidenceValidationError) as changed_source:
        validate_evidence(graph, SOURCE_TEXT.replace("kidney", "hepatic"))
    assert changed_source.value.code == "source_length_mismatch"

    same_length_change = SOURCE_TEXT.replace("diabetes", "diabetas")
    with pytest.raises(EvidenceValidationError) as changed_hash:
        validate_evidence(graph, same_length_change)
    assert changed_hash.value.code == "source_hash_mismatch"


def test_nested_all_any_permutations_have_identical_canonical_hashes() -> None:
    age = _predicate("Age 18 years")
    diabetes = _predicate("diabetes")
    kidney = _predicate("kidney disease")
    left = _graph(
        AllOf(
            kind="all_of",
            children=(age, AnyOf(kind="any_of", children=(diabetes, kidney))),
        )
    )
    right = _graph(
        AllOf(
            kind="all_of",
            children=(AnyOf(kind="any_of", children=(kidney, diabetes)), age),
        )
    )

    assert canonical_graph_json(left) == canonical_graph_json(right)
    assert canonical_graph_sha256(left) == canonical_graph_sha256(right)
    assert canonicalize_graph(canonicalize_graph(left)) == canonicalize_graph(left)


def test_at_least_child_order_is_canonicalized_as_commutative() -> None:
    diabetes = _predicate("diabetes")
    kidney = _predicate("kidney disease")
    left = _graph(AtLeast(kind="at_least", minimum=1, children=(diabetes, kidney)))
    right = _graph(AtLeast(kind="at_least", minimum=1, children=(kidney, diabetes)))

    assert canonical_graph_json(left) == canonical_graph_json(right)
    assert canonical_graph_sha256(left) == canonical_graph_sha256(right)


def test_not_position_and_modifier_order_remain_semantically_visible() -> None:
    diabetes = _predicate("diabetes")
    kidney = _predicate("kidney disease")
    modifier_a = Modifier(kind=ModifierKind.STATUS, name="first")
    modifier_b = Modifier(kind=ModifierKind.SEVERITY, name="second")
    predicate_ab = diabetes.model_copy(update={"modifiers": (modifier_a, modifier_b)})
    predicate_ba = diabetes.model_copy(update={"modifiers": (modifier_b, modifier_a)})

    assert canonical_graph_sha256(_graph(predicate_ab)) != canonical_graph_sha256(
        _graph(predicate_ba)
    )
    assert canonical_graph_sha256(_graph(Not(kind="not", child=diabetes))) != (
        canonical_graph_sha256(_graph(Not(kind="not", child=kidney)))
    )


def test_numeric_predicate_uses_explicit_scalar_type_and_strict_value() -> None:
    age = Predicate(
        kind="predicate",
        concept=Concept(text="age"),
        comparator=Comparator.GREATER_THAN_OR_EQUAL,
        value=ScalarValue(kind="scalar", data_type=ScalarType.INTEGER, value=18),
        evidence=(_span("Age 18 years"),),
    )
    assert _graph(age).root == age

    with pytest.raises(ValidationError, match="data_type"):
        ScalarValue(kind="scalar", data_type="integer", value="18")


def test_numeric_and_date_ranges_reject_reversed_bounds() -> None:
    with pytest.raises(ValidationError, match="lower bound"):
        RangeValue(
            kind="range",
            lower=ScalarValue(kind="scalar", data_type="integer", value=19),
            upper=ScalarValue(kind="scalar", data_type="integer", value=18),
            lower_inclusive=True,
            upper_inclusive=True,
        )

    with pytest.raises(ValidationError, match="lower bound"):
        RangeValue(
            kind="range",
            lower=ScalarValue(kind="scalar", data_type="date", value="2026-09-02"),
            upper=ScalarValue(kind="scalar", data_type="date", value="2026-09-01"),
            lower_inclusive=True,
            upper_inclusive=True,
        )


def test_review_and_machine_executable_states_cannot_contradict_each_other() -> None:
    source = SourceDocument.from_text(
        trial_id="NCT-REVIEW",
        document_id="review-criterion",
        text="Clinical judgement required",
    )
    with pytest.raises(ValidationError, match="machine-executable"):
        EligibilityGraphV2(
            schema_version="2.0",
            source=source,
            criterion_id="review-criterion",
            criterion_kind="unknown",
            root=None,
            review_required=False,
            review_reasons=(),
            not_machine_executable=False,
        )

    graph = EligibilityGraphV2(
        schema_version="2.0",
        source=source,
        criterion_id="review-criterion",
        criterion_kind="unknown",
        root=None,
        review_required=True,
        review_reasons=("Requires clinician judgement",),
        not_machine_executable=True,
    )
    assert graph.root is None


def test_models_reject_unknown_fields_and_do_not_coerce_strict_primitives() -> None:
    with pytest.raises(ValidationError, match="extra"):
        EvidenceSpanV2(start_char=0, end_char=5, quote="Adult", unknown=True)  # type: ignore[call-arg]
    with pytest.raises(ValidationError):
        EvidenceSpanV2(start_char="0", end_char=5, quote="Adult")


def test_canonical_json_has_sorted_keys_no_incidental_whitespace_and_valid_json() -> None:
    serialized = canonical_graph_json(_graph(_predicate("diabetes")))

    assert "\n" not in serialized
    assert ": " not in serialized
    assert json.loads(serialized)["schema_version"] == "2.0"


def test_flat_output_schema_is_non_recursive_and_contains_no_trusted_identity() -> None:
    schema = FlatGraphOutputV2.model_json_schema()
    serialized = json.dumps(schema, sort_keys=True)

    for forbidden_property in (
        "criterion_id",
        "document_id",
        "source_url",
        "text_length",
        "text_sha256",
        "trial_id",
    ):
        assert f'"{forbidden_property}"' not in serialized

    definitions = schema.get("$defs", {})
    dependencies: dict[str, set[str]] = {}

    def collect_refs(value: object) -> set[str]:
        if isinstance(value, dict):
            refs = {
                item.rsplit("/", maxsplit=1)[-1]
                for key, item in value.items()
                if key == "$ref" and isinstance(item, str)
            }
            return refs.union(*(collect_refs(item) for item in value.values()))
        if isinstance(value, list):
            return set().union(*(collect_refs(item) for item in value))
        return set()

    for name, definition in definitions.items():
        dependencies[name] = collect_refs(definition)

    visited: set[str] = set()
    active: set[str] = set()

    def visit(name: str) -> None:
        if name in active:
            raise AssertionError(f"recursive JSON Schema definition: {name}")
        if name in visited:
            return
        active.add(name)
        for dependency in dependencies.get(name, set()):
            visit(dependency)
        active.remove(name)
        visited.add(name)

    for definition_name in definitions:
        visit(definition_name)


def test_flat_strict_schema_removes_prior_nested_defaults_and_discriminator() -> None:
    raw_schema = FlatGraphOutputV2.model_json_schema()
    raw_snapshot = deepcopy(raw_schema)
    raw_definitions = raw_schema["$defs"]

    assert raw_definitions["Concept"]["properties"]["normalized"]["default"] is None
    assert raw_definitions["Unit"]["properties"]["system"]["default"] is None
    assert raw_definitions["TemporalConstraintV2"]["properties"]["quantity"]["default"] is None
    assert raw_definitions["Modifier"]["properties"]["value"]["default"] is None
    assert "discriminator" in raw_definitions["FlatExpressionNodeV2"]
    assert "oneOf" in raw_definitions["FlatExpressionNodeV2"]

    strict_schema = flat_graph_strict_json_schema()

    assert raw_schema == raw_snapshot
    assert strict_schema == strict_output_schema(raw_schema)
    strict_definitions = strict_schema["$defs"]
    assert "anyOf" in strict_definitions["FlatExpressionNodeV2"]
    assert "oneOf" not in strict_definitions["FlatExpressionNodeV2"]

    stack: list[object] = [strict_schema]
    while stack:
        value = stack.pop()
        if isinstance(value, list):
            stack.extend(value)
            continue
        if not isinstance(value, dict):
            continue
        assert "default" not in value
        assert "discriminator" not in value
        assert "oneOf" not in value
        if value.get("type") == "object":
            properties = value.get("properties")
            assert isinstance(properties, dict)
            assert value.get("required") == list(properties)
            assert value.get("additionalProperties") is False
        stack.extend(value.values())


def test_flat_strict_schema_has_a_deterministic_regression_hash() -> None:
    serialized = json.dumps(
        flat_graph_strict_json_schema(),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )

    assert hashlib.sha256(serialized.encode("utf-8")).hexdigest() == (
        "2d6ccd6c8fa4092f67c892d854db4a98201367778c365e47f5715e60b6ebd712"
    )


def test_complete_strict_provider_json_still_parses_as_flat_graph_output() -> None:
    output = _flat_output(
        root_node_id="root",
        nodes=(
            FlatPredicateNodeV2(
                node_id="root",
                kind="predicate",
                concept=Concept(
                    text="metformin",
                    normalized=None,
                    system=None,
                    code=None,
                ),
                comparator=Comparator.EQUAL,
                value=ScalarValue(kind="scalar", data_type="integer", value=500),
                unit=Unit(text="mg", system=None, code=None),
                temporal=(
                    TemporalConstraintV2(
                        relation=TemporalRelationV2.WITHIN,
                        quantity=ScalarValue(
                            kind="scalar",
                            data_type="integer",
                            value=6,
                        ),
                        unit=Unit(text="months", system=None, code=None),
                        reference_event=None,
                        evidence=(),
                    ),
                ),
                modifiers=(
                    Modifier(
                        kind=ModifierKind.FREQUENCY,
                        name="frequency",
                        value=ScalarValue(
                            kind="scalar",
                            data_type="string",
                            value="daily",
                        ),
                        unit=None,
                        evidence=(),
                    ),
                ),
                evidence=(_span("diabetes"),),
            ),
        ),
    )
    provider_json = output.model_dump_json()

    assert FlatGraphOutputV2.model_validate_json(provider_json) == output


def test_strict_schema_transform_fails_closed_on_invalid_root_and_composition() -> None:
    with pytest.raises(ValueError, match="root"):
        strict_output_schema({"type": "string"})

    with pytest.raises(ValueError, match="allOf"):
        strict_output_schema(
            {
                "type": "object",
                "properties": {
                    "invalid": {
                        "allOf": [{"type": "string"}, {"type": "string"}],
                    },
                },
            }
        )


def test_strict_schema_transform_enforces_documented_size_limits() -> None:
    too_deep: dict[str, object] = {"type": "string"}
    for _ in range(11):
        too_deep = {
            "type": "object",
            "properties": {"child": too_deep},
        }
    with pytest.raises(ValueError, match="10-level"):
        strict_output_schema(too_deep)

    with pytest.raises(ValueError, match="5000-property"):
        strict_output_schema(
            {
                "type": "object",
                "properties": {f"property_{index}": {"type": "string"} for index in range(5_001)},
            }
        )

    with pytest.raises(ValueError, match="1000-value"):
        strict_output_schema(
            {
                "type": "object",
                "properties": {
                    "choice": {
                        "type": "string",
                        "enum": [f"value_{index}" for index in range(1_001)],
                    },
                },
            }
        )

    with pytest.raises(ValueError, match="120000-character"):
        strict_output_schema(
            {
                "type": "object",
                "properties": {"x" * 120_001: {"type": "string"}},
            }
        )


def test_flat_output_inflates_only_after_trusted_identity_is_injected() -> None:
    diabetes = _flat_predicate("diabetes-node", "diabetes")
    kidney = _flat_predicate("kidney-node", "kidney disease")
    conjunction = FlatAllOfNodeV2(
        node_id="root-node",
        kind="all_of",
        child_node_ids=("diabetes-node", "kidney-node"),
        evidence=(),
    )
    output = _flat_output(
        root_node_id="root-node",
        nodes=(kidney, conjunction, diabetes),
    )
    trusted_source = SourceDocument.from_text(
        trial_id="NCT-TRUSTED",
        document_id="trusted-document",
        text=SOURCE_TEXT,
    )

    graph = inflate_model_output(
        output,
        source=trusted_source,
        criterion_id="trusted-criterion",
        criterion_kind=CriterionKindV2.EXCLUSION,
    )

    assert graph.source == trusted_source
    assert graph.criterion_id == "trusted-criterion"
    assert graph.criterion_kind is CriterionKindV2.EXCLUSION
    assert isinstance(graph.root, AllOf)
    predicate_texts = [
        child.concept.text for child in graph.root.children if isinstance(child, Predicate)
    ]
    assert predicate_texts == [
        "diabetes",
        "kidney disease",
    ]
    validate_evidence(graph, SOURCE_TEXT)


def test_flat_output_rejects_provider_supplied_identity_fields() -> None:
    predicate = _flat_predicate("root", "diabetes")
    payload = {
        "schema_version": "2.0",
        "root_node_id": "root",
        "nodes": [predicate.model_dump(mode="json")],
        "review_required": False,
        "review_reasons": [],
        "not_machine_executable": False,
        "trial_id": "NCT-UNTRUSTED",
    }

    with pytest.raises(ValidationError, match="extra"):
        FlatGraphOutputV2.model_validate(payload)


@pytest.mark.parametrize(
    ("nodes", "root_node_id", "message"),
    [
        (
            (
                _flat_predicate("duplicate", "diabetes"),
                _flat_predicate("duplicate", "kidney disease"),
            ),
            "duplicate",
            "unique",
        ),
        ((_flat_predicate("actual", "diabetes"),), "missing", "root node"),
        (
            (
                FlatNotNodeV2(
                    node_id="root",
                    kind="not",
                    child_node_id="missing",
                    evidence=(),
                ),
            ),
            "root",
            "child node",
        ),
        (
            (
                FlatNotNodeV2(
                    node_id="first",
                    kind="not",
                    child_node_id="second",
                    evidence=(),
                ),
                FlatNotNodeV2(
                    node_id="second",
                    kind="not",
                    child_node_id="first",
                    evidence=(),
                ),
            ),
            "first",
            "acyclic",
        ),
        (
            (
                _flat_predicate("root", "diabetes"),
                _flat_predicate("orphan", "kidney disease"),
            ),
            "root",
            "unreachable",
        ),
    ],
)
def test_flat_output_rejects_invalid_node_tables(
    nodes: tuple[object, ...],
    root_node_id: str,
    message: str,
) -> None:
    with pytest.raises(ValidationError, match=message):
        _flat_output(root_node_id=root_node_id, nodes=nodes)


def test_flat_output_rejects_duplicate_child_references() -> None:
    with pytest.raises(ValidationError, match="unique"):
        FlatAllOfNodeV2(
            node_id="root",
            kind="all_of",
            child_node_ids=("child", "child"),
            evidence=(),
        )


def test_flat_output_without_root_is_only_valid_for_empty_unexecutable_case() -> None:
    output = _flat_output(
        root_node_id=None,
        nodes=(),
        review_required=True,
        review_reasons=("Requires clinician judgement",),
        not_machine_executable=True,
    )
    graph = inflate_model_output(
        output,
        source=SourceDocument.from_text(
            trial_id="NCT-REVIEW",
            document_id="review-document",
            text="Clinical judgement required",
        ),
        criterion_id="review-criterion",
        criterion_kind=CriterionKindV2.UNKNOWN,
    )
    assert graph.root is None

    with pytest.raises(ValidationError, match="cannot contain nodes"):
        _flat_output(
            root_node_id=None,
            nodes=(_flat_predicate("orphan", "diabetes"),),
            review_required=True,
            review_reasons=("Not executable",),
            not_machine_executable=True,
        )


def test_flat_validation_inflation_and_canonicalization_handle_maximum_safe_depth() -> None:
    depth = MAX_FLAT_GRAPH_DEPTH - 1
    nodes: list[object] = [_flat_predicate(f"node-{depth}", "diabetes")]
    nodes.extend(
        FlatNotNodeV2(
            node_id=f"node-{index}",
            kind="not",
            child_node_id=f"node-{index + 1}",
            evidence=(),
        )
        for index in range(depth - 1, -1, -1)
    )
    output = _flat_output(root_node_id="node-0", nodes=tuple(nodes))

    graph = inflate_model_output(
        output,
        source=SourceDocument.from_text(
            trial_id="NCT-DEEP",
            document_id="deep-document",
            text=SOURCE_TEXT,
        ),
        criterion_id="deep-criterion",
        criterion_kind=CriterionKindV2.INCLUSION,
    )

    assert sum(1 for _ in iter_expressions(graph.root)) == depth + 1
    assert len(canonical_graph_sha256(graph)) == 64


def test_flat_output_rejects_excessive_depth_and_shared_child_dags() -> None:
    depth = MAX_FLAT_GRAPH_DEPTH
    deep_nodes: list[object] = [_flat_predicate(f"node-{depth}", "diabetes")]
    deep_nodes.extend(
        FlatNotNodeV2(
            node_id=f"node-{index}",
            kind="not",
            child_node_id=f"node-{index + 1}",
            evidence=(),
        )
        for index in range(depth - 1, -1, -1)
    )
    with pytest.raises(ValidationError, match="depth cannot exceed"):
        _flat_output(root_node_id="node-0", nodes=tuple(deep_nodes))

    shared = _flat_predicate("shared", "diabetes")
    left = FlatNotNodeV2(node_id="left", kind="not", child_node_id="shared", evidence=())
    right = FlatNotNodeV2(node_id="right", kind="not", child_node_id="shared", evidence=())
    root = FlatAllOfNodeV2(
        node_id="root",
        kind="all_of",
        child_node_ids=("left", "right"),
        evidence=(),
    )
    with pytest.raises(ValidationError, match="shared child"):
        _flat_output(root_node_id="root", nodes=(root, left, right, shared))


def test_internal_graph_rejects_excessive_depth_and_shared_nodes() -> None:
    nested: object = _predicate("diabetes")
    for _index in range(MAX_FLAT_GRAPH_DEPTH):
        nested = Not(kind="not", child=nested, evidence=())
    with pytest.raises(ValidationError, match="depth cannot exceed"):
        _graph(nested)

    shared = _predicate("diabetes")
    with pytest.raises(ValidationError, match="shared nodes"):
        _graph(AllOf(kind="all_of", children=(shared, shared), evidence=()))


def test_set_value_item_order_is_semantically_canonical() -> None:
    first = _graph(
        Predicate(
            kind="predicate",
            concept=Concept(text="condition"),
            comparator=Comparator.IN,
            value=SetValue(
                kind="set",
                items=(
                    ScalarValue(kind="scalar", data_type=ScalarType.STRING, value="A"),
                    ScalarValue(kind="scalar", data_type=ScalarType.STRING, value="B"),
                ),
            ),
            evidence=(_span("diabetes"),),
        )
    )
    second = first.model_copy(
        update={
            "root": first.root.model_copy(
                update={
                    "value": SetValue(
                        kind="set",
                        items=tuple(reversed(first.root.value.items)),
                    )
                }
            )
        }
    )
    assert canonical_graph_sha256(first) == canonical_graph_sha256(second)
