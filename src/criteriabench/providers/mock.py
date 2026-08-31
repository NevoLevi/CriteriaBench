"""Deterministic, zero-cost extraction provider used by tests and local development."""

from __future__ import annotations

import re
from dataclasses import dataclass

from criteriabench.domain.schemas import (
    ClinicalTrialEligibility,
    ComparisonOperator,
    CriterionCategory,
    CriterionKind,
    EligibilityCriterion,
    EvidenceSpan,
    LogicConnector,
    LogicGroup,
    TemporalConstraint,
    TemporalRelation,
    TemporalUnit,
    TrialDocument,
)
from criteriabench.providers.base import ExtractionProvider, ProviderResult, TokenUsage

_BULLET_PREFIX = re.compile(r"^\s*(?:[-*\u2022]|\d+[.)])\s*")
_SECTION_LINE = re.compile(
    r"^\s*(?:key\s+)?(?P<section>inclusion|exclusion)\s+criteria\s*:?[\s]*$",
    re.IGNORECASE,
)
_NEGATION = re.compile(r"\b(?:no|not|without|never|must\s+not|cannot)\b", re.IGNORECASE)


@dataclass(frozen=True, slots=True)
class _Candidate:
    kind: CriterionKind
    text: str
    start: int
    end: int


class DeterministicMockProvider(ExtractionProvider):
    """Simple rule baseline that provides reproducible benchmark plumbing."""

    name = "mock"
    model = "deterministic-rules-v1"

    async def extract(self, trial: TrialDocument) -> ProviderResult:
        candidates = _split_criteria(trial.eligibility_text)
        inclusion: list[EligibilityCriterion] = []
        exclusion: list[EligibilityCriterion] = []
        for candidate in candidates:
            target = inclusion if candidate.kind is CriterionKind.INCLUSION else exclusion
            target.append(_normalize_candidate(candidate, len(target) + 1))

        extraction = ClinicalTrialEligibility(
            schema_version="1.0",
            trial_id=trial.trial_id,
            inclusion_criteria=inclusion,
            exclusion_criteria=exclusion,
            ambiguities=(
                [] if candidates else ["No bullet-like eligibility criteria were detected."]
            ),
        )
        return ProviderResult(
            extraction=extraction,
            provider=self.name,
            model=self.model,
            latency_ms=0.0,
            usage=TokenUsage(),
            estimated_cost_usd=0.0,
        )


def _split_criteria(text: str) -> list[_Candidate]:
    current_kind = CriterionKind.INCLUSION
    candidates: list[_Candidate] = []
    cursor = 0
    for raw_line in text.splitlines(keepends=True):
        line_without_newline = raw_line.rstrip("\r\n")
        section_match = _SECTION_LINE.match(line_without_newline)
        if section_match:
            current_kind = CriterionKind(section_match.group("section").lower())
            cursor += len(raw_line)
            continue

        stripped = _BULLET_PREFIX.sub("", line_without_newline).strip()
        if stripped and _BULLET_PREFIX.match(line_without_newline):
            local_start = line_without_newline.find(stripped)
            start = cursor + local_start
            candidates.append(
                _Candidate(
                    kind=current_kind,
                    text=stripped,
                    start=start,
                    end=start + len(stripped),
                )
            )
        cursor += len(raw_line)
    return candidates


def _normalize_candidate(candidate: _Candidate, position: int) -> EligibilityCriterion:
    normalized_source = " ".join(candidate.text.split())
    category = _infer_category(normalized_source)
    operator, value, unit = _infer_comparison(normalized_source)
    prefix = "I" if candidate.kind is CriterionKind.INCLUSION else "E"
    return EligibilityCriterion(
        criterion_id=f"{prefix}{position:03d}",
        kind=candidate.kind,
        category=category,
        source_text=candidate.text,
        normalized_text=normalized_source.rstrip(".").casefold(),
        concept=_infer_concept(normalized_source, category),
        operator=operator,
        value=value,
        unit=unit,
        negated=bool(_NEGATION.search(normalized_source)),
        temporal_constraint=_infer_temporal_constraint(normalized_source),
        logic_group=LogicGroup(
            group_id=f"{prefix}G{position:03d}",
            connector=LogicConnector.SINGLE,
            parent_group_id=None,
        ),
        evidence=EvidenceSpan(
            start_char=candidate.start,
            end_char=candidate.end,
            quote=candidate.text,
        ),
    )


def _infer_category(text: str) -> CriterionCategory:
    lowered = text.casefold()
    rules = (
        (CriterionCategory.AGE, ("age", "years old")),
        (CriterionCategory.PERFORMANCE_STATUS, ("ecog", "karnofsky", "performance status")),
        (CriterionCategory.LABORATORY, ("hemoglobin", "platelet", "creatinine", "bilirubin")),
        (CriterionCategory.REPRODUCTIVE, ("pregnan", "contraception", "childbearing")),
        (CriterionCategory.CONSENT, ("consent",)),
        (CriterionCategory.MEDICATION, ("medication", "therapy", "drug", "chemotherapy")),
        (CriterionCategory.PROCEDURE, ("surgery", "procedure", "biopsy")),
        (CriterionCategory.DIAGNOSIS, ("diagnos", "histolog", "cancer", "carcinoma")),
    )
    for category, terms in rules:
        if any(term in lowered for term in terms):
            return category
    return CriterionCategory.OTHER


def _infer_comparison(
    text: str,
) -> tuple[ComparisonOperator, str | int | float | None, str | None]:
    patterns: tuple[tuple[re.Pattern[str], ComparisonOperator], ...] = (
        (
            re.compile(r"(?:>=|at least|minimum(?: of)?)\s*(\d+(?:\.\d+)?)", re.I),
            ComparisonOperator.GREATER_THAN_OR_EQUAL,
        ),
        (
            re.compile(
                r"(?:<=|no more than|maximum(?: of)?|up to)\s*(\d+(?:\.\d+)?)",
                re.I,
            ),
            ComparisonOperator.LESS_THAN_OR_EQUAL,
        ),
        (re.compile(r">\s*(\d+(?:\.\d+)?)", re.I), ComparisonOperator.GREATER_THAN),
        (re.compile(r"<\s*(\d+(?:\.\d+)?)", re.I), ComparisonOperator.LESS_THAN),
    )
    for pattern, operator in patterns:
        match = pattern.search(text)
        if match:
            raw_value = match.group(1)
            value: int | float = float(raw_value) if "." in raw_value else int(raw_value)
            unit_match = re.search(
                rf"{re.escape(raw_value)}\s*(years?|months?|days?|mg|g/dl|x10\^9/l)",
                text,
                re.I,
            )
            return operator, value, unit_match.group(1).lower() if unit_match else None
    return ComparisonOperator.UNSPECIFIED, None, None


def _infer_concept(text: str, category: CriterionCategory) -> str:
    if category is CriterionCategory.AGE:
        return "age"
    if category is CriterionCategory.PERFORMANCE_STATUS and "ecog" in text.casefold():
        return "ECOG performance status"
    words = re.findall(r"[A-Za-z][A-Za-z-]+", text)
    return " ".join(words[:8]) or category.value


def _infer_temporal_constraint(text: str) -> TemporalConstraint:
    patterns: tuple[tuple[re.Pattern[str], TemporalRelation], ...] = (
        (
            re.compile(
                r"\b(?:within|in the (?:past|last))\s+(\d+(?:\.\d+)?)\s+"
                r"(minutes?|hours?|days?|weeks?|months?|years?)"
                r"(?:\s+(?:before|prior to)\s+([^,.;]+))?",
                re.IGNORECASE,
            ),
            TemporalRelation.WITHIN_PREVIOUS,
        ),
        (
            re.compile(
                r"\bfor at least\s+(\d+(?:\.\d+)?)\s+"
                r"(minutes?|hours?|days?|weeks?|months?|years?)",
                re.IGNORECASE,
            ),
            TemporalRelation.FOR_AT_LEAST,
        ),
    )
    for pattern, relation in patterns:
        match = pattern.search(text)
        if match:
            raw_quantity = match.group(1)
            quantity: int | float = (
                float(raw_quantity) if "." in raw_quantity else int(raw_quantity)
            )
            unit = TemporalUnit(_pluralize_temporal_unit(match.group(2)))
            reference_event = (
                match.group(3).strip() if match.lastindex == 3 and match.group(3) else None
            )
            return TemporalConstraint(
                relation=relation,
                quantity=quantity,
                unit=unit,
                reference_event=reference_event,
                raw_text=match.group(0),
            )
    return TemporalConstraint(
        relation=TemporalRelation.UNSPECIFIED,
        quantity=None,
        unit=None,
        reference_event=None,
        raw_text="",
    )


def _pluralize_temporal_unit(unit: str) -> str:
    lowered = unit.casefold()
    return lowered if lowered.endswith("s") else f"{lowered}s"
