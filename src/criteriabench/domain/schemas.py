"""Strict, versioned schemas for eligibility-criteria extraction."""

from __future__ import annotations

from collections import Counter
from enum import StrEnum
from numbers import Real
from typing import Annotated, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StrictFloat,
    StrictInt,
    model_validator,
)


class StrictModel(BaseModel):
    """Base model that rejects unexpected fields at system boundaries."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class CriterionKind(StrEnum):
    INCLUSION = "inclusion"
    EXCLUSION = "exclusion"


class CriterionCategory(StrEnum):
    AGE = "age"
    DEMOGRAPHIC = "demographic"
    DIAGNOSIS = "diagnosis"
    DISEASE_STATE = "disease_state"
    LABORATORY = "laboratory"
    MEDICATION = "medication"
    PROCEDURE = "procedure"
    PERFORMANCE_STATUS = "performance_status"
    REPRODUCTIVE = "reproductive"
    CONSENT = "consent"
    OTHER = "other"


class ComparisonOperator(StrEnum):
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
    UNSPECIFIED = "unspecified"


class TemporalRelation(StrEnum):
    BEFORE = "before"
    AFTER = "after"
    WITHIN_PREVIOUS = "within_previous"
    DURING = "during"
    SINCE = "since"
    UNTIL = "until"
    FOR_AT_LEAST = "for_at_least"
    UNSPECIFIED = "unspecified"


class TemporalUnit(StrEnum):
    MINUTES = "minutes"
    HOURS = "hours"
    DAYS = "days"
    WEEKS = "weeks"
    MONTHS = "months"
    YEARS = "years"


class LogicConnector(StrEnum):
    SINGLE = "single"
    AND = "and"
    OR = "or"


class EvidenceSpan(StrictModel):
    """Exact provenance for a normalized criterion."""

    start_char: Annotated[StrictInt, Field(ge=0)]
    end_char: Annotated[StrictInt, Field(gt=0)]
    quote: Annotated[str, Field(min_length=1, max_length=10_000)]

    @model_validator(mode="after")
    def end_is_after_start(self) -> EvidenceSpan:
        if self.end_char <= self.start_char:
            raise ValueError("end_char must be greater than start_char")
        return self


class TemporalConstraint(StrictModel):
    """Structured timing attached to an eligibility statement."""

    relation: TemporalRelation
    quantity: Annotated[StrictInt | StrictFloat, Field(gt=0)] | None
    unit: TemporalUnit | None
    reference_event: Annotated[str, Field(max_length=300)] | None
    raw_text: Annotated[str, Field(max_length=1_000)]

    @model_validator(mode="after")
    def relation_fields_are_consistent(self) -> TemporalConstraint:
        has_quantity = self.quantity is not None
        has_unit = self.unit is not None
        if has_quantity != has_unit:
            raise ValueError("temporal quantity and unit must be provided together")

        duration_relations = {
            TemporalRelation.WITHIN_PREVIOUS,
            TemporalRelation.FOR_AT_LEAST,
        }
        event_relations = {
            TemporalRelation.BEFORE,
            TemporalRelation.AFTER,
            TemporalRelation.DURING,
            TemporalRelation.SINCE,
            TemporalRelation.UNTIL,
        }
        if self.relation is TemporalRelation.UNSPECIFIED:
            if has_quantity or self.reference_event is not None:
                raise ValueError("unspecified temporal relation cannot carry duration or reference")
        elif self.relation in duration_relations and not has_quantity:
            raise ValueError("temporal duration relation requires quantity and unit")
        elif self.relation in event_relations and self.reference_event is None:
            raise ValueError("temporal event relation requires reference_event")
        return self


GroupId = Annotated[str, Field(pattern=r"^[IE]G[0-9]{3}$")]


class LogicGroup(StrictModel):
    """Logical relationship shared by one or more extracted criteria."""

    group_id: GroupId
    connector: LogicConnector
    parent_group_id: GroupId | None

    @model_validator(mode="after")
    def cannot_parent_itself(self) -> LogicGroup:
        if self.parent_group_id == self.group_id:
            raise ValueError("logic group cannot be its own parent")
        return self


CriterionScalar = str | int | float | bool
CriterionValue = CriterionScalar | list[str | int | float] | None


class EligibilityCriterion(StrictModel):
    """One independently evaluable eligibility statement."""

    criterion_id: Annotated[str, Field(pattern=r"^[IE][0-9]{3}$")]
    kind: CriterionKind
    category: CriterionCategory
    source_text: Annotated[str, Field(min_length=1, max_length=10_000)]
    normalized_text: Annotated[str, Field(min_length=1, max_length=10_000)]
    concept: Annotated[str, Field(min_length=1, max_length=300)]
    operator: ComparisonOperator
    value: CriterionValue
    unit: Annotated[str, Field(max_length=100)] | None
    negated: StrictBool
    temporal_constraint: TemporalConstraint
    logic_group: LogicGroup
    evidence: EvidenceSpan

    @model_validator(mode="after")
    def fields_are_semantically_consistent(self) -> EligibilityCriterion:
        prefix = "I" if self.kind is CriterionKind.INCLUSION else "E"
        if not self.criterion_id.startswith(prefix):
            raise ValueError("criterion identifier prefix must match criterion kind")
        if not self.logic_group.group_id.startswith(prefix):
            raise ValueError("logic group prefix must match criterion kind")
        parent = self.logic_group.parent_group_id
        if parent is not None and not parent.startswith(prefix):
            raise ValueError("parent logic group prefix must match criterion kind")
        if self.source_text != self.evidence.quote:
            raise ValueError("source_text must equal the evidence quote")

        numeric_operators = {
            ComparisonOperator.GREATER_THAN,
            ComparisonOperator.GREATER_THAN_OR_EQUAL,
            ComparisonOperator.LESS_THAN,
            ComparisonOperator.LESS_THAN_OR_EQUAL,
        }
        if self.operator in numeric_operators and not _is_number(self.value):
            raise ValueError("numeric operator requires a numeric value")
        if self.operator is ComparisonOperator.BETWEEN:
            if not isinstance(self.value, list) or len(self.value) != 2:
                raise ValueError("between operator requires exactly two value bounds")
            left, right = self.value
            compatible = (_is_number(left) and _is_number(right)) or (
                isinstance(left, str) and isinstance(right, str)
            )
            if not compatible:
                raise ValueError("between operator requires compatible value bounds")
        if self.operator in {ComparisonOperator.IN, ComparisonOperator.NOT_IN}:
            if not isinstance(self.value, list) or not self.value:
                raise ValueError("in operators require a non-empty value list")
        if self.operator in {ComparisonOperator.EXISTS, ComparisonOperator.NOT_EXISTS}:
            if not isinstance(self.value, bool):
                raise ValueError("existence operator requires a boolean value")
        if self.operator is ComparisonOperator.UNSPECIFIED:
            if self.value is not None or self.unit is not None:
                raise ValueError("unspecified operator requires value and unit to be null")
        return self


class ClinicalTrialEligibility(StrictModel):
    """Complete structured output for one clinical trial."""

    schema_version: Literal["1.0"]
    trial_id: Annotated[str, Field(min_length=1, max_length=100)]
    inclusion_criteria: list[EligibilityCriterion]
    exclusion_criteria: list[EligibilityCriterion]
    ambiguities: list[str]

    @model_validator(mode="after")
    def criteria_are_consistent(self) -> ClinicalTrialEligibility:
        criteria = self.inclusion_criteria + self.exclusion_criteria
        identifiers = [criterion.criterion_id for criterion in criteria]
        if len(set(identifiers)) != len(identifiers):
            raise ValueError("criterion identifiers must be unique")

        for criterion in self.inclusion_criteria:
            if criterion.kind is not CriterionKind.INCLUSION:
                raise ValueError("inclusion_criteria may contain only inclusion criteria")
        for criterion in self.exclusion_criteria:
            if criterion.kind is not CriterionKind.EXCLUSION:
                raise ValueError("exclusion_criteria may contain only exclusion criteria")

        groups: dict[str, LogicGroup] = {}
        counts: Counter[str] = Counter()
        for criterion in criteria:
            group = criterion.logic_group
            existing = groups.get(group.group_id)
            if existing is not None and existing != group:
                raise ValueError("shared logic group definitions must be identical")
            groups[group.group_id] = group
            counts[group.group_id] += 1

        for group_id, group in groups.items():
            if group.connector is LogicConnector.SINGLE and counts[group_id] != 1:
                raise ValueError("a single logic group must contain exactly one criterion")
            if group.connector in {LogicConnector.AND, LogicConnector.OR} and counts[group_id] < 2:
                raise ValueError("and/or logic groups require multiple criteria")
            parent = group.parent_group_id
            if parent is not None and parent not in groups:
                raise ValueError("parent logic group must exist in the extraction")

        _validate_group_cycles(groups)
        return self


def _is_number(value: object) -> bool:
    return isinstance(value, Real) and not isinstance(value, bool)


def _validate_group_cycles(groups: dict[str, LogicGroup]) -> None:
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(group_id: str) -> None:
        if group_id in visiting:
            raise ValueError("logic group hierarchy contains a cycle")
        if group_id in visited:
            return
        visiting.add(group_id)
        parent = groups[group_id].parent_group_id
        if parent is not None:
            visit(parent)
        visiting.remove(group_id)
        visited.add(group_id)

    for group_id in groups:
        visit(group_id)


class TrialDocument(StrictModel):
    """Minimal public trial representation consumed by extraction providers."""

    trial_id: Annotated[str, Field(min_length=1, max_length=100)]
    title: Annotated[str, Field(min_length=1, max_length=2_000)]
    eligibility_text: Annotated[str, Field(min_length=1, max_length=100_000)]
    source_url: str | None = None


class ExtractionRequest(StrictModel):
    """Synchronous extraction request."""

    trial: TrialDocument
    persist: bool = True
