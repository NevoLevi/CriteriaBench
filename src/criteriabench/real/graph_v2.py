"""Versioned semantic graph for real clinical-trial eligibility criteria.

The v2 graph is intentionally separate from the legacy extraction schema.  It is
an immutable semantic boundary for corpus import, model output, and evaluation.
Character offsets are Python/Unicode code-point offsets using a half-open range.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Iterator, Mapping, Sequence
from copy import deepcopy
from datetime import date, datetime
from enum import StrEnum
from typing import Annotated, Any, Literal, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StrictFloat,
    StrictInt,
    StrictStr,
    model_validator,
)


class GraphModel(BaseModel):
    """Immutable model that rejects unknown fields at every graph boundary."""

    model_config = ConfigDict(extra="forbid", frozen=True)


class CriterionKindV2(StrEnum):
    INCLUSION = "inclusion"
    EXCLUSION = "exclusion"
    UNKNOWN = "unknown"


class Comparator(StrEnum):
    EQUAL = "equal"
    NOT_EQUAL = "not_equal"
    GREATER_THAN = "greater_than"
    GREATER_THAN_OR_EQUAL = "greater_than_or_equal"
    LESS_THAN = "less_than"
    LESS_THAN_OR_EQUAL = "less_than_or_equal"
    BETWEEN = "between"
    IN = "in"
    NOT_IN = "not_in"
    EXISTS = "exists"
    NOT_EXISTS = "not_exists"
    CONTAINS = "contains"
    NOT_CONTAINS = "not_contains"
    STARTS_WITH = "starts_with"
    ENDS_WITH = "ends_with"
    MATCHES = "matches"
    UNSPECIFIED = "unspecified"


class ScalarType(StrEnum):
    STRING = "string"
    INTEGER = "integer"
    NUMBER = "number"
    BOOLEAN = "boolean"
    DATE = "date"
    DATETIME = "datetime"


class TemporalRelationV2(StrEnum):
    BEFORE = "before"
    AFTER = "after"
    WITHIN_BEFORE = "within_before"
    WITHIN_AFTER = "within_after"
    WITHIN = "within"
    DURING = "during"
    SINCE = "since"
    UNTIL = "until"
    FOR_AT_LEAST = "for_at_least"
    FOR_AT_MOST = "for_at_most"
    AT = "at"
    ONGOING = "ongoing"
    UNSPECIFIED = "unspecified"


class ModifierKind(StrEnum):
    ASSERTION = "assertion"
    ANATOMICAL_SITE = "anatomical_site"
    DOSE = "dose"
    EXPERIENCER = "experiencer"
    FREQUENCY = "frequency"
    LATERALITY = "laterality"
    ROUTE = "route"
    SEVERITY = "severity"
    STAGE = "stage"
    STATUS = "status"
    OTHER = "other"


class EvidenceSpanV2(GraphModel):
    """Exact source evidence using a half-open ``[start_char, end_char)`` span."""

    start_char: Annotated[StrictInt, Field(ge=0)]
    end_char: Annotated[StrictInt, Field(gt=0)]
    quote: Annotated[StrictStr, Field(min_length=1, max_length=100_000)]

    @model_validator(mode="after")
    def span_and_quote_have_the_same_length(self) -> Self:
        if self.end_char <= self.start_char:
            raise ValueError("end_char must be greater than start_char")
        if self.end_char - self.start_char != len(self.quote):
            raise ValueError("evidence span length must equal quote length")
        return self


class SourceDocument(GraphModel):
    """Immutable identity for the exact text against which offsets are measured."""

    trial_id: Annotated[StrictStr, Field(min_length=1, max_length=200)]
    document_id: Annotated[StrictStr, Field(min_length=1, max_length=500)]
    text_sha256: Annotated[StrictStr, Field(pattern=r"^[0-9a-f]{64}$")]
    text_length: Annotated[StrictInt, Field(gt=0)]
    source_url: Annotated[StrictStr, Field(min_length=1, max_length=2_000)] | None = None

    @model_validator(mode="after")
    def identifiers_are_not_only_whitespace(self) -> Self:
        if not self.trial_id.strip() or not self.document_id.strip():
            raise ValueError("source identifiers cannot be blank")
        if self.source_url is not None and not self.source_url.strip():
            raise ValueError("source_url cannot be blank")
        return self

    @classmethod
    def from_text(
        cls,
        *,
        trial_id: str,
        document_id: str,
        text: str,
        source_url: str | None = None,
    ) -> Self:
        """Create source metadata from the exact Unicode text used for offsets."""

        if not text:
            raise ValueError("source text cannot be empty")
        return cls(
            trial_id=trial_id,
            document_id=document_id,
            text_sha256=hashlib.sha256(text.encode("utf-8")).hexdigest(),
            text_length=len(text),
            source_url=source_url,
        )


class Concept(GraphModel):
    """Surface concept with optional normalized terminology identity."""

    text: Annotated[StrictStr, Field(min_length=1, max_length=2_000)]
    normalized: Annotated[StrictStr, Field(min_length=1, max_length=2_000)] | None = None
    system: Annotated[StrictStr, Field(min_length=1, max_length=500)] | None = None
    code: Annotated[StrictStr, Field(min_length=1, max_length=500)] | None = None

    @model_validator(mode="after")
    def terminology_fields_are_consistent(self) -> Self:
        if not self.text.strip():
            raise ValueError("concept text cannot be blank")
        if (self.system is None) != (self.code is None):
            raise ValueError("concept terminology system and code must be supplied together")
        return self


class Unit(GraphModel):
    """Surface unit with an optional code-system identity."""

    text: Annotated[StrictStr, Field(min_length=1, max_length=200)]
    system: Annotated[StrictStr, Field(min_length=1, max_length=500)] | None = None
    code: Annotated[StrictStr, Field(min_length=1, max_length=500)] | None = None

    @model_validator(mode="after")
    def unit_fields_are_consistent(self) -> Self:
        if not self.text.strip():
            raise ValueError("unit text cannot be blank")
        if (self.system is None) != (self.code is None):
            raise ValueError("unit terminology system and code must be supplied together")
        return self


ScalarPrimitive = StrictStr | StrictInt | StrictFloat | StrictBool
MAX_FLAT_GRAPH_DEPTH = 64


class ScalarValue(GraphModel):
    """A scalar whose declared semantic type must match its strict JSON value."""

    kind: Literal["scalar"]
    data_type: ScalarType
    value: ScalarPrimitive

    @model_validator(mode="after")
    def declared_type_matches_value(self) -> Self:
        value_type = type(self.value)
        expected_types: dict[ScalarType, tuple[type[Any], ...]] = {
            ScalarType.STRING: (str,),
            ScalarType.INTEGER: (int,),
            ScalarType.NUMBER: (float,),
            ScalarType.BOOLEAN: (bool,),
            ScalarType.DATE: (str,),
            ScalarType.DATETIME: (str,),
        }
        if value_type not in expected_types[self.data_type]:
            raise ValueError("scalar data_type does not match its strict JSON value")
        if isinstance(self.value, float) and not math.isfinite(self.value):
            raise ValueError("numeric scalar value must be finite")
        if self.data_type is ScalarType.DATE:
            try:
                date.fromisoformat(str(self.value))
            except ValueError as error:
                raise ValueError("date scalar must use ISO 8601 YYYY-MM-DD format") from error
        if self.data_type is ScalarType.DATETIME:
            try:
                datetime.fromisoformat(str(self.value))
            except ValueError as error:
                raise ValueError("datetime scalar must use ISO 8601 format") from error
        return self


class RangeValue(GraphModel):
    """Ordered lower/upper bounds; bound order is semantically significant."""

    kind: Literal["range"]
    lower: ScalarValue
    upper: ScalarValue
    lower_inclusive: StrictBool
    upper_inclusive: StrictBool

    @model_validator(mode="after")
    def bounds_are_compatible_and_ordered(self) -> Self:
        if self.lower.data_type is not self.upper.data_type:
            raise ValueError("range bounds must have the same data_type")
        if self.lower.data_type not in {
            ScalarType.INTEGER,
            ScalarType.NUMBER,
            ScalarType.DATE,
            ScalarType.DATETIME,
        }:
            raise ValueError("range bounds must be numeric, date, or datetime scalars")
        if _range_lower_exceeds_upper(self.lower, self.upper):
            raise ValueError("range lower bound cannot exceed upper bound")
        return self


class SetValue(GraphModel):
    """A homogeneous membership set whose order is irrelevant to semantics."""

    kind: Literal["set"]
    items: Annotated[tuple[ScalarValue, ...], Field(min_length=1)]

    @model_validator(mode="after")
    def items_are_homogeneous_and_unique(self) -> Self:
        item_types = {item.data_type for item in self.items}
        if len(item_types) != 1:
            raise ValueError("set items must have the same data_type")
        identities = [
            (item.data_type, type(item.value).__name__, item.value) for item in self.items
        ]
        if len(set(identities)) != len(identities):
            raise ValueError("set items must be unique")
        return self


type PredicateValue = Annotated[
    ScalarValue | RangeValue | SetValue,
    Field(discriminator="kind"),
]


class TemporalConstraintV2(GraphModel):
    """Timing relation attached to a predicate, with explicit duration and anchor."""

    relation: TemporalRelationV2
    quantity: ScalarValue | RangeValue | None = None
    unit: Unit | None = None
    reference_event: Concept | None = None
    evidence: tuple[EvidenceSpanV2, ...] = ()

    @model_validator(mode="after")
    def temporal_fields_are_consistent(self) -> Self:
        if (self.quantity is None) != (self.unit is None):
            raise ValueError("temporal quantity and unit must be supplied together")
        if self.quantity is not None and not _is_numeric_value(self.quantity):
            raise ValueError("temporal quantity must be numeric")

        reference_relations = {
            TemporalRelationV2.BEFORE,
            TemporalRelationV2.AFTER,
            TemporalRelationV2.WITHIN_BEFORE,
            TemporalRelationV2.WITHIN_AFTER,
            TemporalRelationV2.DURING,
            TemporalRelationV2.SINCE,
            TemporalRelationV2.UNTIL,
            TemporalRelationV2.AT,
        }
        duration_relations = {
            TemporalRelationV2.WITHIN,
            TemporalRelationV2.WITHIN_BEFORE,
            TemporalRelationV2.WITHIN_AFTER,
            TemporalRelationV2.FOR_AT_LEAST,
            TemporalRelationV2.FOR_AT_MOST,
        }
        if self.relation is TemporalRelationV2.UNSPECIFIED:
            if self.quantity is not None or self.reference_event is not None:
                raise ValueError("unspecified temporal relation cannot carry details")
        if self.relation in reference_relations and self.reference_event is None:
            raise ValueError("temporal relation requires a reference_event")
        if self.relation in duration_relations and self.quantity is None:
            raise ValueError("temporal relation requires a quantity and unit")
        return self


class Modifier(GraphModel):
    """Ordered predicate qualifier with a typed value and optional provenance."""

    kind: ModifierKind
    name: Annotated[StrictStr, Field(min_length=1, max_length=500)]
    value: PredicateValue | None = None
    unit: Unit | None = None
    evidence: tuple[EvidenceSpanV2, ...] = ()

    @model_validator(mode="after")
    def modifier_fields_are_consistent(self) -> Self:
        if not self.name.strip():
            raise ValueError("modifier name cannot be blank")
        if self.unit is not None and not _is_numeric_value(self.value):
            raise ValueError("a modifier unit requires a numeric value")
        return self


class Predicate(GraphModel):
    """One typed, evidence-grounded proposition."""

    kind: Literal["predicate"]
    concept: Concept
    comparator: Comparator
    value: PredicateValue | None = None
    unit: Unit | None = None
    temporal: tuple[TemporalConstraintV2, ...] = ()
    modifiers: tuple[Modifier, ...] = ()
    evidence: Annotated[tuple[EvidenceSpanV2, ...], Field(min_length=1)]

    @model_validator(mode="after")
    def comparator_and_value_are_consistent(self) -> Self:
        no_value = {Comparator.EXISTS, Comparator.NOT_EXISTS, Comparator.UNSPECIFIED}
        if self.comparator in no_value and self.value is not None:
            raise ValueError("existence/unspecified comparator cannot carry a value")
        if self.comparator not in no_value and self.value is None:
            raise ValueError("comparator requires a value")
        if self.comparator is Comparator.BETWEEN and not isinstance(self.value, RangeValue):
            raise ValueError("between comparator requires a range value")
        if self.comparator in {Comparator.IN, Comparator.NOT_IN} and not isinstance(
            self.value, SetValue
        ):
            raise ValueError("membership comparator requires a set value")
        scalar_comparators = {
            Comparator.GREATER_THAN,
            Comparator.GREATER_THAN_OR_EQUAL,
            Comparator.LESS_THAN,
            Comparator.LESS_THAN_OR_EQUAL,
        }
        if self.comparator in scalar_comparators:
            if not isinstance(self.value, ScalarValue) or self.value.data_type not in {
                ScalarType.INTEGER,
                ScalarType.NUMBER,
                ScalarType.DATE,
                ScalarType.DATETIME,
            }:
                raise ValueError("ordered comparator requires a numeric/date scalar")
        if self.unit is not None and not _is_numeric_value(self.value):
            raise ValueError("a predicate unit requires a numeric value")
        return self


class AllOf(GraphModel):
    """Commutative conjunction."""

    kind: Literal["all_of"]
    children: Annotated[tuple[Expression, ...], Field(min_length=2)]
    evidence: tuple[EvidenceSpanV2, ...] = ()


class AnyOf(GraphModel):
    """Commutative disjunction."""

    kind: Literal["any_of"]
    children: Annotated[tuple[Expression, ...], Field(min_length=2)]
    evidence: tuple[EvidenceSpanV2, ...] = ()


class Not(GraphModel):
    """Unary logical negation; its child position is never reordered."""

    kind: Literal["not"]
    child: Expression
    evidence: tuple[EvidenceSpanV2, ...] = ()


class AtLeast(GraphModel):
    """Commutative k-of-n expression."""

    kind: Literal["at_least"]
    minimum: Annotated[StrictInt, Field(ge=1)]
    children: Annotated[tuple[Expression, ...], Field(min_length=1)]
    evidence: tuple[EvidenceSpanV2, ...] = ()

    @model_validator(mode="after")
    def minimum_does_not_exceed_child_count(self) -> Self:
        if self.minimum > len(self.children):
            raise ValueError("at_least minimum cannot exceed its number of children")
        return self


type Expression = Annotated[
    AllOf | AnyOf | Not | AtLeast | Predicate,
    Field(discriminator="kind"),
]


class FlatPredicateNodeV2(GraphModel):
    """Non-recursive provider representation of a predicate node."""

    node_id: Annotated[StrictStr, Field(min_length=1, max_length=500)]
    kind: Literal["predicate"]
    concept: Concept
    comparator: Comparator
    value: PredicateValue | None
    unit: Unit | None
    temporal: tuple[TemporalConstraintV2, ...]
    modifiers: tuple[Modifier, ...]
    evidence: Annotated[tuple[EvidenceSpanV2, ...], Field(min_length=1)]

    @model_validator(mode="after")
    def node_and_predicate_fields_are_valid(self) -> Self:
        _ensure_nonblank_node_id(self.node_id)
        Predicate(
            kind="predicate",
            concept=self.concept,
            comparator=self.comparator,
            value=self.value,
            unit=self.unit,
            temporal=self.temporal,
            modifiers=self.modifiers,
            evidence=self.evidence,
        )
        return self


class FlatAllOfNodeV2(GraphModel):
    """Non-recursive provider representation of a conjunction."""

    node_id: Annotated[StrictStr, Field(min_length=1, max_length=500)]
    kind: Literal["all_of"]
    child_node_ids: Annotated[tuple[StrictStr, ...], Field(min_length=2)]
    evidence: tuple[EvidenceSpanV2, ...]

    @model_validator(mode="after")
    def node_and_child_ids_are_valid(self) -> Self:
        _validate_flat_node_identifiers(self.node_id, self.child_node_ids)
        return self


class FlatAnyOfNodeV2(GraphModel):
    """Non-recursive provider representation of a disjunction."""

    node_id: Annotated[StrictStr, Field(min_length=1, max_length=500)]
    kind: Literal["any_of"]
    child_node_ids: Annotated[tuple[StrictStr, ...], Field(min_length=2)]
    evidence: tuple[EvidenceSpanV2, ...]

    @model_validator(mode="after")
    def node_and_child_ids_are_valid(self) -> Self:
        _validate_flat_node_identifiers(self.node_id, self.child_node_ids)
        return self


class FlatNotNodeV2(GraphModel):
    """Non-recursive provider representation of unary negation."""

    node_id: Annotated[StrictStr, Field(min_length=1, max_length=500)]
    kind: Literal["not"]
    child_node_id: Annotated[StrictStr, Field(min_length=1, max_length=500)]
    evidence: tuple[EvidenceSpanV2, ...]

    @model_validator(mode="after")
    def node_and_child_id_are_valid(self) -> Self:
        _validate_flat_node_identifiers(self.node_id, (self.child_node_id,))
        return self


class FlatAtLeastNodeV2(GraphModel):
    """Non-recursive provider representation of a commutative k-of-n node."""

    node_id: Annotated[StrictStr, Field(min_length=1, max_length=500)]
    kind: Literal["at_least"]
    minimum: Annotated[StrictInt, Field(ge=1)]
    child_node_ids: Annotated[tuple[StrictStr, ...], Field(min_length=1)]
    evidence: tuple[EvidenceSpanV2, ...]

    @model_validator(mode="after")
    def node_child_ids_and_minimum_are_valid(self) -> Self:
        _validate_flat_node_identifiers(self.node_id, self.child_node_ids)
        if self.minimum > len(self.child_node_ids):
            raise ValueError("at_least minimum cannot exceed its number of children")
        return self


type FlatExpressionNodeV2 = Annotated[
    FlatAllOfNodeV2 | FlatAnyOfNodeV2 | FlatNotNodeV2 | FlatAtLeastNodeV2 | FlatPredicateNodeV2,
    Field(discriminator="kind"),
]


class FlatGraphOutputV2(GraphModel):
    """Identity-free, non-recursive graph returned by a model provider.

    Trusted source and criterion identity deliberately do not cross this boundary.
    Node references are validated as one finite, fully reachable directed acyclic
    graph before the output can be inflated into the internal recursive model.
    """

    schema_version: Literal["2.0"]
    root_node_id: Annotated[StrictStr, Field(min_length=1, max_length=500)] | None
    nodes: Annotated[tuple[FlatExpressionNodeV2, ...], Field(max_length=10_000)]
    review_required: StrictBool
    review_reasons: tuple[Annotated[StrictStr, Field(min_length=1, max_length=2_000)], ...]
    not_machine_executable: StrictBool

    @model_validator(mode="after")
    def state_and_node_table_are_valid(self) -> Self:
        if self.review_required != bool(self.review_reasons):
            raise ValueError("review_required must exactly match the presence of review_reasons")
        if any(not reason.strip() for reason in self.review_reasons):
            raise ValueError("review reasons cannot be blank")
        if self.not_machine_executable and not self.review_required:
            raise ValueError("not_machine_executable criteria must require review")
        if self.root_node_id is None:
            if not self.not_machine_executable:
                raise ValueError("a machine-executable criterion requires a root node")
            if self.nodes:
                raise ValueError("a graph without a root node cannot contain nodes")
            return self
        _ensure_nonblank_node_id(self.root_node_id)

        nodes_by_id: dict[str, FlatExpressionNodeV2] = {}
        for node in self.nodes:
            if node.node_id in nodes_by_id:
                raise ValueError("flat graph node IDs must be unique")
            nodes_by_id[node.node_id] = node
        if self.root_node_id not in nodes_by_id:
            raise ValueError("flat graph root node does not exist")

        parent_by_child: dict[str, str] = {}
        for node in self.nodes:
            for child_node_id in _flat_child_node_ids(node):
                if child_node_id not in nodes_by_id:
                    raise ValueError("flat graph child node does not exist")
                previous_parent = parent_by_child.setdefault(child_node_id, node.node_id)
                if previous_parent != node.node_id:
                    raise ValueError("flat graph must be a tree; shared child nodes are forbidden")

        postorder = _flat_postorder(self.root_node_id, nodes_by_id)
        if len(postorder) != len(nodes_by_id):
            raise ValueError("flat graph contains nodes unreachable from its root")
        if _flat_maximum_depth(postorder, nodes_by_id) > MAX_FLAT_GRAPH_DEPTH:
            raise ValueError(f"flat graph depth cannot exceed {MAX_FLAT_GRAPH_DEPTH} nodes")
        return self


def strict_output_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """Return a strict Structured Outputs schema without mutating its input."""

    strict_schema = deepcopy(schema)
    _rewrite_strict_schema(strict_schema)
    _lint_strict_schema(strict_schema)
    return strict_schema


def flat_graph_strict_json_schema() -> dict[str, Any]:
    """Build the deterministic strict provider schema for FlatGraphOutputV2."""

    return strict_output_schema(FlatGraphOutputV2.model_json_schema())


class EligibilityGraphV2(GraphModel):
    """Complete semantic representation of one source eligibility criterion."""

    schema_version: Literal["2.0"]
    source: SourceDocument
    criterion_id: Annotated[StrictStr, Field(min_length=1, max_length=500)]
    criterion_kind: CriterionKindV2
    root: Expression | None
    review_required: StrictBool
    review_reasons: tuple[Annotated[StrictStr, Field(min_length=1, max_length=2_000)], ...]
    not_machine_executable: StrictBool

    @model_validator(mode="after")
    def graph_state_and_evidence_are_consistent(self) -> Self:
        if not self.criterion_id.strip():
            raise ValueError("criterion_id cannot be blank")
        if self.review_required != bool(self.review_reasons):
            raise ValueError("review_required must exactly match the presence of review_reasons")
        if any(not reason.strip() for reason in self.review_reasons):
            raise ValueError("review reasons cannot be blank")
        if self.not_machine_executable and not self.review_required:
            raise ValueError("not_machine_executable criteria must require review")
        if self.root is None and not self.not_machine_executable:
            raise ValueError("a machine-executable criterion requires a root expression")
        if self.root is not None:
            _validate_expression_tree(self.root)
        for _, evidence in iter_evidence(self.root):
            if evidence.end_char > self.source.text_length:
                raise ValueError("evidence span exceeds source text length")
        return self


def inflate_model_output(
    output: FlatGraphOutputV2,
    *,
    source: SourceDocument,
    criterion_id: str,
    criterion_kind: CriterionKindV2,
) -> EligibilityGraphV2:
    """Inject trusted case identity and iteratively inflate a provider node table."""

    root: Expression | None = None
    if output.root_node_id is not None:
        nodes_by_id = {node.node_id: node for node in output.nodes}
        expressions_by_id: dict[str, Expression] = {}
        for node_id in _flat_postorder(output.root_node_id, nodes_by_id):
            node = nodes_by_id[node_id]
            if isinstance(node, FlatPredicateNodeV2):
                expression: Expression = _predicate_from_flat_node(node)
            elif isinstance(node, FlatAllOfNodeV2):
                expression = AllOf(
                    kind="all_of",
                    children=tuple(
                        expressions_by_id[child_node_id] for child_node_id in node.child_node_ids
                    ),
                    evidence=node.evidence,
                )
            elif isinstance(node, FlatAnyOfNodeV2):
                expression = AnyOf(
                    kind="any_of",
                    children=tuple(
                        expressions_by_id[child_node_id] for child_node_id in node.child_node_ids
                    ),
                    evidence=node.evidence,
                )
            elif isinstance(node, FlatNotNodeV2):
                expression = Not(
                    kind="not",
                    child=expressions_by_id[node.child_node_id],
                    evidence=node.evidence,
                )
            else:
                expression = AtLeast(
                    kind="at_least",
                    minimum=node.minimum,
                    children=tuple(
                        expressions_by_id[child_node_id] for child_node_id in node.child_node_ids
                    ),
                    evidence=node.evidence,
                )
            expressions_by_id[node_id] = expression
        root = expressions_by_id[output.root_node_id]

    return EligibilityGraphV2(
        schema_version=output.schema_version,
        source=source,
        criterion_id=criterion_id,
        criterion_kind=criterion_kind,
        root=root,
        review_required=output.review_required,
        review_reasons=output.review_reasons,
        not_machine_executable=output.not_machine_executable,
    )


class EvidenceValidationError(ValueError):
    """Stable, content-free diagnostic for source/evidence validation failures."""

    def __init__(self, code: str, *, path: str | None = None) -> None:
        self.code = code
        self.path = path
        suffix = "" if path is None else f" at {path}"
        super().__init__(f"{code}{suffix}")


def iter_expressions(root: Expression | None) -> Iterator[tuple[str, Expression]]:
    """Yield expressions in deterministic pre-order without changing supplied order."""

    if root is None:
        return

    stack: list[tuple[str, Expression]] = [("root", root)]
    while stack:
        path, node = stack.pop()
        yield path, node
        if isinstance(node, (AllOf, AnyOf, AtLeast)):
            for index in range(len(node.children) - 1, -1, -1):
                stack.append((f"{path}.children[{index}]", node.children[index]))
        elif isinstance(node, Not):
            stack.append((f"{path}.child", node.child))


def iter_evidence(root: Expression | None) -> Iterator[tuple[str, EvidenceSpanV2]]:
    """Yield every node, temporal, and modifier evidence span with a stable path."""

    for path, node in iter_expressions(root):
        for index, evidence in enumerate(node.evidence):
            yield f"{path}.evidence[{index}]", evidence
        if isinstance(node, Predicate):
            for temporal_index, temporal in enumerate(node.temporal):
                for evidence_index, evidence in enumerate(temporal.evidence):
                    yield (
                        f"{path}.temporal[{temporal_index}].evidence[{evidence_index}]",
                        evidence,
                    )
            for modifier_index, modifier in enumerate(node.modifiers):
                for evidence_index, evidence in enumerate(modifier.evidence):
                    yield (
                        f"{path}.modifiers[{modifier_index}].evidence[{evidence_index}]",
                        evidence,
                    )


def canonicalize_expression(expression: Expression) -> Expression:
    """Iteratively canonicalize only explicitly commutative structures."""

    _validate_expression_tree(expression)
    canonical_by_identity: dict[int, Expression] = {}
    stack: list[tuple[Expression, bool]] = [(expression, False)]
    while stack:
        node, children_visited = stack.pop()
        if not children_visited:
            stack.append((node, True))
            if isinstance(node, (AllOf, AnyOf, AtLeast)):
                stack.extend((child, False) for child in reversed(node.children))
            elif isinstance(node, Not):
                stack.append((node.child, False))
            continue

        if isinstance(node, (AllOf, AnyOf, AtLeast)):
            children = tuple(canonical_by_identity[id(child)] for child in node.children)
            canonical: Expression = node.model_copy(
                update={"children": tuple(sorted(children, key=_expression_sort_key))}
            )
        elif isinstance(node, Not):
            canonical = node.model_copy(update={"child": canonical_by_identity[id(node.child)]})
        else:
            canonical = node.model_copy(
                update={
                    "value": _canonicalize_predicate_value(node.value),
                    "modifiers": tuple(
                        modifier.model_copy(
                            update={"value": _canonicalize_predicate_value(modifier.value)}
                        )
                        for modifier in node.modifiers
                    ),
                }
            )
        canonical_by_identity[id(node)] = canonical

    return canonical_by_identity[id(expression)]


def canonicalize_graph(graph: EligibilityGraphV2) -> EligibilityGraphV2:
    """Return an immutable graph with recursively canonicalized commutative nodes."""

    root = None if graph.root is None else canonicalize_expression(graph.root)
    return graph.model_copy(update={"root": root})


def canonical_graph_json(graph: EligibilityGraphV2) -> str:
    """Serialize a graph deterministically after safe semantic canonicalization."""

    canonical = canonicalize_graph(graph)
    return _canonical_json(canonical.model_dump(mode="json"))


def canonical_graph_sha256(graph: EligibilityGraphV2) -> str:
    """Hash the canonical UTF-8 JSON representation of a graph."""

    return hashlib.sha256(canonical_graph_json(graph).encode("utf-8")).hexdigest()


def validate_evidence(graph: EligibilityGraphV2, source_text: str) -> None:
    """Verify the exact source identity and every evidence quote/offset pair."""

    if len(source_text) != graph.source.text_length:
        raise EvidenceValidationError("source_length_mismatch")
    digest = hashlib.sha256(source_text.encode("utf-8")).hexdigest()
    if digest != graph.source.text_sha256:
        raise EvidenceValidationError("source_hash_mismatch")
    for path, evidence in iter_evidence(graph.root):
        if source_text[evidence.start_char : evidence.end_char] != evidence.quote:
            raise EvidenceValidationError("quote_mismatch", path=path)


def _is_numeric_value(value: PredicateValue | None) -> bool:
    if isinstance(value, ScalarValue):
        return value.data_type in {ScalarType.INTEGER, ScalarType.NUMBER}
    if isinstance(value, RangeValue):
        return value.lower.data_type in {ScalarType.INTEGER, ScalarType.NUMBER}
    return False


def _comparable_scalar(value: ScalarValue) -> int | float | date | datetime:
    if value.data_type is ScalarType.DATE:
        return date.fromisoformat(str(value.value))
    if value.data_type is ScalarType.DATETIME:
        return datetime.fromisoformat(str(value.value))
    if isinstance(value.value, bool) or not isinstance(value.value, (int, float)):
        raise TypeError("scalar is not orderable")
    return value.value


def _range_lower_exceeds_upper(lower: ScalarValue, upper: ScalarValue) -> bool:
    if lower.data_type is ScalarType.DATE:
        return date.fromisoformat(str(lower.value)) > date.fromisoformat(str(upper.value))
    if lower.data_type is ScalarType.DATETIME:
        return datetime.fromisoformat(str(lower.value)) > datetime.fromisoformat(str(upper.value))
    left = _comparable_scalar(lower)
    right = _comparable_scalar(upper)
    if isinstance(left, (date, datetime)) or isinstance(right, (date, datetime)):
        raise TypeError("range bounds are not comparable")
    return left > right


def _ensure_nonblank_node_id(node_id: str) -> None:
    if not node_id.strip():
        raise ValueError("flat graph node IDs cannot be blank")
    if len(node_id) > 500:
        raise ValueError("flat graph node IDs cannot exceed 500 characters")


def _validate_flat_node_identifiers(node_id: str, child_node_ids: tuple[str, ...]) -> None:
    _ensure_nonblank_node_id(node_id)
    for child_node_id in child_node_ids:
        _ensure_nonblank_node_id(child_node_id)
    if len(set(child_node_ids)) != len(child_node_ids):
        raise ValueError("flat graph child node references must be unique")


def _flat_child_node_ids(node: FlatExpressionNodeV2) -> tuple[str, ...]:
    if isinstance(node, (FlatAllOfNodeV2, FlatAnyOfNodeV2, FlatAtLeastNodeV2)):
        return node.child_node_ids
    if isinstance(node, FlatNotNodeV2):
        return (node.child_node_id,)
    return ()


def _flat_postorder(
    root_node_id: str,
    nodes_by_id: dict[str, FlatExpressionNodeV2],
) -> list[str]:
    """Return children-first order with iterative tri-color cycle detection."""

    visit_state: dict[str, int] = {root_node_id: 1}
    postorder: list[str] = []
    stack: list[tuple[str, int]] = [(root_node_id, 0)]
    while stack:
        node_id, child_index = stack[-1]
        child_node_ids = _flat_child_node_ids(nodes_by_id[node_id])
        if child_index >= len(child_node_ids):
            visit_state[node_id] = 2
            postorder.append(node_id)
            stack.pop()
            continue

        child_node_id = child_node_ids[child_index]
        stack[-1] = (node_id, child_index + 1)
        child_state = visit_state.get(child_node_id, 0)
        if child_state == 1:
            raise ValueError("flat graph must be acyclic")
        if child_state == 0:
            visit_state[child_node_id] = 1
            stack.append((child_node_id, 0))
    return postorder


def _flat_maximum_depth(
    postorder: Sequence[str],
    nodes_by_id: Mapping[str, FlatExpressionNodeV2],
) -> int:
    """Compute root-to-leaf node depth from an already validated postorder."""

    depths: dict[str, int] = {}
    maximum = 0
    for node_id in postorder:
        child_node_ids = _flat_child_node_ids(nodes_by_id[node_id])
        depth = 1 + max((depths[child_id] for child_id in child_node_ids), default=0)
        depths[node_id] = depth
        maximum = max(maximum, depth)
    return maximum


def _validate_expression_tree(root: Expression) -> None:
    """Reject recursive-model DAGs and unsafe depth before traversal or serialization."""

    seen: set[int] = set()
    stack: list[tuple[Expression, int]] = [(root, 1)]
    while stack:
        node, depth = stack.pop()
        identity = id(node)
        if identity in seen:
            raise ValueError("eligibility graph must be a tree; shared nodes are forbidden")
        seen.add(identity)
        if depth > MAX_FLAT_GRAPH_DEPTH:
            raise ValueError(f"eligibility graph depth cannot exceed {MAX_FLAT_GRAPH_DEPTH} nodes")
        if isinstance(node, (AllOf, AnyOf, AtLeast)):
            stack.extend((child, depth + 1) for child in node.children)
        elif isinstance(node, Not):
            stack.append((node.child, depth + 1))


def _canonicalize_predicate_value(value: PredicateValue | None) -> PredicateValue | None:
    if not isinstance(value, SetValue):
        return value
    return value.model_copy(update={"items": tuple(sorted(value.items, key=_scalar_sort_key))})


def _predicate_from_flat_node(node: FlatPredicateNodeV2) -> Predicate:
    return Predicate(
        kind="predicate",
        concept=node.concept,
        comparator=node.comparator,
        value=node.value,
        unit=node.unit,
        temporal=node.temporal,
        modifiers=node.modifiers,
        evidence=node.evidence,
    )


_STRICT_SCHEMA_KEYWORDS = frozenset(
    {
        "$defs",
        "$ref",
        "additionalProperties",
        "anyOf",
        "const",
        "description",
        "enum",
        "exclusiveMaximum",
        "exclusiveMinimum",
        "format",
        "items",
        "maxItems",
        "maxLength",
        "maximum",
        "minItems",
        "minLength",
        "minimum",
        "multipleOf",
        "pattern",
        "properties",
        "required",
        "title",
        "type",
    }
)
_UNSUPPORTED_STRICT_SCHEMA_KEYWORDS = frozenset(
    {
        "allOf",
        "dependentRequired",
        "dependentSchemas",
        "else",
        "if",
        "not",
        "oneOf",
        "then",
    }
)
_STRICT_SCHEMA_MAX_PROPERTIES = 5_000
_STRICT_SCHEMA_MAX_DEPTH = 10
_STRICT_SCHEMA_MAX_STRING_SIZE = 120_000
_STRICT_SCHEMA_MAX_ENUM_VALUES = 1_000
_STRICT_SCHEMA_LARGE_ENUM_THRESHOLD = 250
_STRICT_SCHEMA_MAX_LARGE_ENUM_STRING_SIZE = 15_000
_JSON_SCHEMA_TYPES = frozenset(
    {"array", "boolean", "integer", "null", "number", "object", "string"}
)


def _rewrite_strict_schema(value: object) -> None:
    if isinstance(value, list):
        for item in value:
            _rewrite_strict_schema(item)
        return
    if not isinstance(value, dict):
        return

    value.pop("default", None)
    value.pop("discriminator", None)
    one_of = value.pop("oneOf", None)
    if one_of is not None:
        if "anyOf" in value:
            raise ValueError("schema cannot contain both oneOf and anyOf")
        value["anyOf"] = one_of
    for item in tuple(value.values()):
        _rewrite_strict_schema(item)

    if _schema_includes_type(value, "object"):
        properties = value.setdefault("properties", {})
        if isinstance(properties, dict):
            value["required"] = list(properties)
            value["additionalProperties"] = False


def _lint_strict_schema(schema: dict[str, Any]) -> None:
    if schema.get("type") != "object" or "anyOf" in schema:
        raise ValueError("strict output schema root must be an object and cannot use anyOf")

    counts = {
        "enum_values": 0,
        "properties": 0,
        "strings": 0,
    }
    _lint_strict_schema_node(schema, path="$", counts=counts)
    if counts["properties"] > _STRICT_SCHEMA_MAX_PROPERTIES:
        raise ValueError("strict output schema exceeds the 5000-property limit")
    if counts["strings"] > _STRICT_SCHEMA_MAX_STRING_SIZE:
        raise ValueError("strict output schema exceeds the 120000-character string limit")
    if counts["enum_values"] > _STRICT_SCHEMA_MAX_ENUM_VALUES:
        raise ValueError("strict output schema exceeds the 1000-value enum limit")
    if _strict_schema_max_depth(schema) > _STRICT_SCHEMA_MAX_DEPTH:
        raise ValueError("strict output schema exceeds the 10-level nesting limit")


def _lint_strict_schema_node(
    node: dict[str, Any],
    *,
    path: str,
    counts: dict[str, int],
) -> None:
    forbidden = _UNSUPPORTED_STRICT_SCHEMA_KEYWORDS.intersection(node)
    if forbidden:
        keyword = min(forbidden)
        raise ValueError(f"unsupported strict output schema keyword {keyword!r} at {path}")
    unknown = set(node).difference(_STRICT_SCHEMA_KEYWORDS)
    if unknown:
        keyword = min(unknown)
        raise ValueError(f"unrecognized strict output schema keyword {keyword!r} at {path}")
    if "default" in node or "discriminator" in node:
        raise ValueError(f"strict output schema metadata was not removed at {path}")

    node_type = node.get("type")
    _validate_schema_type(node_type, path=path)

    properties = node.get("properties")
    if _schema_includes_type(node, "object"):
        if not isinstance(properties, dict):
            raise ValueError(f"strict output object must define properties at {path}")
        property_names = list(properties)
        if node.get("required") != property_names:
            raise ValueError(f"strict output object must require every property at {path}")
        if node.get("additionalProperties") is not False:
            raise ValueError(f"strict output object must forbid additional properties at {path}")

    if properties is not None:
        if not isinstance(properties, dict):
            raise ValueError(f"schema properties must be an object at {path}")
        counts["properties"] += len(properties)
        for name, child in properties.items():
            if not isinstance(name, str) or not isinstance(child, dict):
                raise ValueError(f"invalid strict output property at {path}")
            counts["strings"] += len(name)
            _lint_strict_schema_node(child, path=f"{path}.properties[{name!r}]", counts=counts)

    definitions = node.get("$defs")
    if definitions is not None:
        if not isinstance(definitions, dict):
            raise ValueError(f"schema definitions must be an object at {path}")
        for name, child in definitions.items():
            if not isinstance(name, str) or not isinstance(child, dict):
                raise ValueError(f"invalid strict output definition at {path}")
            counts["strings"] += len(name)
            _lint_strict_schema_node(child, path=f"{path}.$defs[{name!r}]", counts=counts)

    items = node.get("items")
    if items is not None:
        if not isinstance(items, dict):
            raise ValueError(f"strict output array items must use one schema at {path}")
        _lint_strict_schema_node(items, path=f"{path}.items", counts=counts)

    alternatives = node.get("anyOf")
    if alternatives is not None:
        if not isinstance(alternatives, list) or not alternatives:
            raise ValueError(f"strict output anyOf must contain schemas at {path}")
        for index, child in enumerate(alternatives):
            if not isinstance(child, dict):
                raise ValueError(f"invalid strict output anyOf branch at {path}")
            _lint_strict_schema_node(child, path=f"{path}.anyOf[{index}]", counts=counts)

    enum_values = node.get("enum")
    if enum_values is not None:
        if not isinstance(enum_values, list) or not enum_values:
            raise ValueError(f"strict output enum must contain values at {path}")
        counts["enum_values"] += len(enum_values)
        enum_string_size = sum(len(value) for value in enum_values if isinstance(value, str))
        counts["strings"] += enum_string_size
        if (
            len(enum_values) > _STRICT_SCHEMA_LARGE_ENUM_THRESHOLD
            and enum_string_size > _STRICT_SCHEMA_MAX_LARGE_ENUM_STRING_SIZE
        ):
            raise ValueError(f"strict output schema has an oversized enum at {path}")

    const_value = node.get("const")
    if isinstance(const_value, str):
        counts["strings"] += len(const_value)

    reference = node.get("$ref")
    if reference is not None and not isinstance(reference, str):
        raise ValueError(f"strict output schema reference must be a string at {path}")


def _validate_schema_type(node_type: object, *, path: str) -> None:
    if node_type is None:
        return
    if isinstance(node_type, str):
        schema_types = {node_type}
    elif (
        isinstance(node_type, list)
        and node_type
        and all(isinstance(item, str) for item in node_type)
    ):
        schema_types = set(node_type)
        if len(schema_types) != len(node_type):
            raise ValueError(f"strict output schema types must be unique at {path}")
    else:
        raise ValueError(f"invalid strict output schema type at {path}")
    if not schema_types.issubset(_JSON_SCHEMA_TYPES):
        raise ValueError(f"unsupported strict output schema type at {path}")


def _schema_includes_type(node: dict[str, Any], expected: str) -> bool:
    node_type = node.get("type")
    return node_type == expected or (isinstance(node_type, list) and expected in node_type)


def _strict_schema_max_depth(schema: dict[str, Any]) -> int:
    definitions = schema.get("$defs")
    if not isinstance(definitions, dict):
        definitions = {}

    def visit(
        node: dict[str, Any],
        *,
        depth: int,
        active_references: frozenset[str],
    ) -> int:
        next_depth = depth + int(_schema_includes_type(node, "object"))
        maximum = next_depth

        reference = node.get("$ref")
        if isinstance(reference, str):
            if reference in active_references:
                return maximum
            prefix = "#/$defs/"
            if not reference.startswith(prefix):
                raise ValueError("strict output schema only supports local definition references")
            definition_name = reference.removeprefix(prefix)
            target = definitions.get(definition_name)
            if not isinstance(target, dict):
                raise ValueError("strict output schema reference does not resolve")
            maximum = max(
                maximum,
                visit(
                    target,
                    depth=depth,
                    active_references=active_references | {reference},
                ),
            )

        properties = node.get("properties")
        if isinstance(properties, dict):
            for child in properties.values():
                if isinstance(child, dict):
                    maximum = max(
                        maximum,
                        visit(child, depth=next_depth, active_references=active_references),
                    )

        items = node.get("items")
        if isinstance(items, dict):
            maximum = max(
                maximum,
                visit(items, depth=next_depth, active_references=active_references),
            )

        alternatives = node.get("anyOf")
        if isinstance(alternatives, list):
            for child in alternatives:
                if isinstance(child, dict):
                    maximum = max(
                        maximum,
                        visit(child, depth=next_depth, active_references=active_references),
                    )
        return maximum

    maximum = visit(schema, depth=0, active_references=frozenset())
    for definition in definitions.values():
        if isinstance(definition, dict):
            maximum = max(
                maximum,
                visit(definition, depth=0, active_references=frozenset()),
            )
    return maximum


def _expression_sort_key(expression: Expression) -> str:
    return _canonical_json(expression.model_dump(mode="json"))


def _scalar_sort_key(value: ScalarValue) -> str:
    return _canonical_json(value.model_dump(mode="json"))


def _canonical_json(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    )


for _model in (AllOf, AnyOf, Not, AtLeast, EligibilityGraphV2):
    _model.model_rebuild()
