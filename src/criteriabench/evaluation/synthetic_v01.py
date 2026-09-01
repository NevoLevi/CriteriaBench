"""Deterministic, non-clinical synthetic reference dataset for CriteriaBench.

The templates in this module are software-test material, not clinical guidance.
Every label remains pending independent review.
"""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from criteriabench.domain.schemas import ClinicalTrialEligibility, TrialDocument

DATASET_VERSION = "synthetic-v0.1"
CASE_COUNT = 80
VARIANTS_PER_FAMILY = 8
FAMILIES = (
    "simple_inclusion_exclusion",
    "numeric_thresholds",
    "temporal_constraints",
    "negation",
    "demographics_consent",
    "laboratory",
    "and_multi_clause",
    "or_multi_clause",
    "range_between",
    "punctuation_evidence_span",
)
DEFAULT_OUTPUT_DIR = Path(__file__).resolve().parents[3] / "data" / "synthetic_v0_1"

Kind = Literal["inclusion", "exclusion"]
Connector = Literal["single", "and", "or"]
CriterionValue = str | int | float | bool | list[str | int | float] | None


@dataclass(frozen=True, slots=True)
class TemporalSeed:
    relation: str = "unspecified"
    quantity: int | float | None = None
    unit: str | None = None
    reference_event: str | None = None
    raw_text: str = ""


@dataclass(frozen=True, slots=True)
class CriterionSeed:
    kind: Kind
    quote: str
    category: str
    concept: str
    operator: str = "unspecified"
    value: CriterionValue = None
    unit: str | None = None
    negated: bool = False
    temporal: TemporalSeed = field(default_factory=TemporalSeed)
    connector: Connector = "single"
    group_number: int = 1


def _span(text: str, quote: str) -> tuple[int, int]:
    start = text.find(quote)
    if start < 0:
        raise ValueError(f"evidence quote is absent from generated text: {quote!r}")
    return start, start + len(quote)


def _criterion(text: str, seed: CriterionSeed, position: int) -> dict[str, Any]:
    start, end = _span(text, seed.quote)
    prefix = "I" if seed.kind == "inclusion" else "E"
    return {
        "criterion_id": f"{prefix}{position:03d}",
        "kind": seed.kind,
        "category": seed.category,
        "source_text": seed.quote,
        "normalized_text": " ".join(seed.quote.casefold().rstrip(".;").split()),
        "concept": seed.concept,
        "operator": seed.operator,
        "value": seed.value,
        "unit": seed.unit,
        "negated": seed.negated,
        "temporal_constraint": {
            "relation": seed.temporal.relation,
            "quantity": seed.temporal.quantity,
            "unit": seed.temporal.unit,
            "reference_event": seed.temporal.reference_event,
            "raw_text": seed.temporal.raw_text,
        },
        "logic_group": {
            "group_id": f"{prefix}G{seed.group_number:03d}",
            "connector": seed.connector,
            "parent_group_id": None,
        },
        "evidence": {"start_char": start, "end_char": end, "quote": seed.quote},
    }


def _fixture(
    case_number: int,
    family: str,
    variant: int,
    text: str,
    seeds: list[CriterionSeed],
    slices: tuple[str, ...],
) -> dict[str, Any]:
    trial_id = f"CB-SYN-V01-{case_number:03d}"
    inclusion_seeds = [seed for seed in seeds if seed.kind == "inclusion"]
    exclusion_seeds = [seed for seed in seeds if seed.kind == "exclusion"]
    inclusion = [
        _criterion(text, seed, position) for position, seed in enumerate(inclusion_seeds, start=1)
    ]
    exclusion = [
        _criterion(text, seed, position) for position, seed in enumerate(exclusion_seeds, start=1)
    ]
    payload: dict[str, Any] = {
        "fixture_version": DATASET_VERSION,
        "trial": {
            "trial_id": trial_id,
            "title": f"Synthetic {family.replace('_', ' ')} case {variant + 1}",
            "eligibility_text": text,
            "source_url": None,
        },
        "reference": {
            "schema_version": "1.0",
            "trial_id": trial_id,
            "inclusion_criteria": inclusion,
            "exclusion_criteria": exclusion,
            "ambiguities": [],
        },
        "provenance": {
            "kind": "synthetic",
            "annotation_method": "deterministic_template_v0.1",
            "family": family,
            "slices": ",".join(slices),
            "review_status": "independent_review_pending",
        },
    }
    TrialDocument.model_validate(payload["trial"])
    ClinicalTrialEligibility.model_validate(payload["reference"])
    return payload


def _simple(case_number: int, variant: int) -> dict[str, Any]:
    inclusions = (
        ("Adults able to attend study visits", "demographic", "adult participation"),
        ("Confirmed solid tumor diagnosis", "diagnosis", "solid tumor diagnosis"),
        ("Able to complete study questionnaires", "other", "questionnaire completion"),
        ("Measurable disease on baseline imaging", "disease_state", "measurable disease"),
        ("Available archival tissue sample", "procedure", "archival tissue availability"),
        ("Stable outpatient status", "disease_state", "stable outpatient status"),
        ("Willing to attend follow-up visits", "other", "follow-up attendance"),
        ("Documented target-condition diagnosis", "diagnosis", "target-condition diagnosis"),
    )
    exclusions = (
        ("Current participation in another study", "other", "concurrent study participation"),
        ("Known allergy to the study drug", "medication", "study drug allergy"),
        ("Uncontrolled intercurrent illness", "disease_state", "intercurrent illness"),
        ("Prior enrollment in this protocol", "other", "prior protocol enrollment"),
        ("Planned major surgery", "procedure", "planned major surgery"),
        ("Active substance-use disorder", "other", "active substance-use disorder"),
        ("Unable to comply with study visits", "other", "visit noncompliance"),
        ("Concurrent investigational therapy", "medication", "investigational therapy"),
    )
    inc_quote, inc_category, inc_concept = inclusions[variant]
    exc_quote, exc_category, exc_concept = exclusions[variant]
    text = f"Inclusion Criteria:\n- {inc_quote}\n\nExclusion Criteria:\n- {exc_quote}"
    return _fixture(
        case_number,
        FAMILIES[0],
        variant,
        text,
        [
            CriterionSeed("inclusion", inc_quote, inc_category, inc_concept),
            CriterionSeed("exclusion", exc_quote, exc_category, exc_concept),
        ],
        ("simple", "inclusion", "exclusion"),
    )


def _numeric(case_number: int, variant: int) -> dict[str, Any]:
    age = (18, 21, 25, 30, 40, 50, 60, 65)[variant]
    status = (2, 1, 2, 1, 2, 1, 2, 1)[variant]
    age_quote = f"Age >= {age} years"
    status_quote = f"ECOG performance status <= {status}"
    text = f"Inclusion Criteria:\n- {age_quote}\n- {status_quote}"
    return _fixture(
        case_number,
        FAMILIES[1],
        variant,
        text,
        [
            CriterionSeed(
                "inclusion",
                age_quote,
                "age",
                "age",
                "greater_than_or_equal",
                age,
                "years",
                group_number=1,
            ),
            CriterionSeed(
                "inclusion",
                status_quote,
                "performance_status",
                "ECOG performance status",
                "less_than_or_equal",
                status,
                group_number=2,
            ),
        ],
        ("numeric_threshold", "comparison", "inclusion"),
    )


def _temporal(case_number: int, variant: int) -> dict[str, Any]:
    stable_weeks = (2, 3, 4, 6, 8, 10, 12, 16)[variant]
    previous_days = (7, 10, 14, 21, 28, 35, 42, 56)[variant]
    inc_quote = f"Stable medication for at least {stable_weeks} weeks"
    exc_quote = f"Chemotherapy within previous {previous_days} days"
    text = f"Inclusion Criteria:\n- {inc_quote}\n\nExclusion Criteria:\n- {exc_quote}"
    return _fixture(
        case_number,
        FAMILIES[2],
        variant,
        text,
        [
            CriterionSeed(
                "inclusion",
                inc_quote,
                "medication",
                "stable medication",
                temporal=TemporalSeed(
                    "for_at_least",
                    stable_weeks,
                    "weeks",
                    None,
                    f"for at least {stable_weeks} weeks",
                ),
            ),
            CriterionSeed(
                "exclusion",
                exc_quote,
                "medication",
                "recent chemotherapy",
                temporal=TemporalSeed(
                    "within_previous",
                    previous_days,
                    "days",
                    None,
                    f"within previous {previous_days} days",
                ),
            ),
        ],
        ("temporal", "duration", "inclusion", "exclusion"),
    )


def _negation(case_number: int, variant: int) -> dict[str, Any]:
    inclusion_items = (
        ("No active infection", "disease_state", "active infection"),
        ("Without uncontrolled hypertension", "disease_state", "uncontrolled hypertension"),
        ("No history of organ transplant", "procedure", "organ transplant history"),
        ("Never received gene therapy", "medication", "prior gene therapy"),
        ("No known immunodeficiency", "diagnosis", "immunodeficiency"),
        ("Without symptomatic heart failure", "disease_state", "symptomatic heart failure"),
        ("No active bleeding", "disease_state", "active bleeding"),
        ("No known study-drug allergy", "medication", "study-drug allergy"),
    )
    exclusion_items = (
        ("Must not receive prohibited medication", "medication", "prohibited medication"),
        ("Cannot undergo planned radiotherapy", "procedure", "planned radiotherapy"),
        ("No concurrent immunosuppressive therapy", "medication", "immunosuppressive therapy"),
        ("Must not be pregnant", "reproductive", "pregnancy"),
        ("Without required contraception", "reproductive", "required contraception"),
        ("Cannot provide follow-up samples", "procedure", "follow-up samples"),
        ("No washout from prior therapy", "medication", "therapy washout"),
        ("Must not use another investigational drug", "medication", "investigational drug"),
    )
    inc_quote, inc_category, inc_concept = inclusion_items[variant]
    exc_quote, exc_category, exc_concept = exclusion_items[variant]
    text = f"Inclusion Criteria:\n- {inc_quote}\n\nExclusion Criteria:\n- {exc_quote}"
    return _fixture(
        case_number,
        FAMILIES[3],
        variant,
        text,
        [
            CriterionSeed(
                "inclusion",
                inc_quote,
                inc_category,
                inc_concept,
                "not_exists",
                False,
                negated=True,
            ),
            CriterionSeed(
                "exclusion",
                exc_quote,
                exc_category,
                exc_concept,
                "not_exists",
                False,
                negated=True,
            ),
        ],
        ("negation", "inclusion", "exclusion"),
    )


def _demographics_consent(case_number: int, variant: int) -> dict[str, Any]:
    age = (18, 19, 21, 25, 30, 40, 50, 65)[variant]
    consent_quote = (
        "Able to provide written informed consent",
        "Willing to sign the informed-consent form",
        "Able to understand the consent discussion",
        "Voluntarily agrees to written informed consent",
        "Can complete the informed-consent process",
        "Willing and able to provide informed consent",
        "Able to document informed consent",
        "Can provide consent before study procedures",
    )[variant]
    age_quote = f"Age >= {age} years"
    text = f"Inclusion Criteria:\n- {age_quote}\n- {consent_quote}"
    return _fixture(
        case_number,
        FAMILIES[4],
        variant,
        text,
        [
            CriterionSeed(
                "inclusion",
                age_quote,
                "age",
                "age",
                "greater_than_or_equal",
                age,
                "years",
                group_number=1,
            ),
            CriterionSeed(
                "inclusion",
                consent_quote,
                "consent",
                "informed consent",
                "exists",
                True,
                group_number=2,
            ),
        ],
        ("demographic", "consent", "numeric_threshold", "inclusion"),
    )


def _laboratory(case_number: int, variant: int) -> dict[str, Any]:
    hemoglobin = (8, 8.5, 9, 9.5, 10, 10.5, 11, 12)[variant]
    platelets = (50, 60, 75, 80, 90, 100, 120, 150)[variant]
    hemoglobin_quote = f"Hemoglobin >= {hemoglobin:.1f} g/dL"
    platelet_quote = f"Platelet count >= {platelets} x10^9/L"
    text = f"Inclusion Criteria:\n- {hemoglobin_quote}\n- {platelet_quote}"
    return _fixture(
        case_number,
        FAMILIES[5],
        variant,
        text,
        [
            CriterionSeed(
                "inclusion",
                hemoglobin_quote,
                "laboratory",
                "hemoglobin",
                "greater_than_or_equal",
                hemoglobin,
                "g/dL",
                group_number=1,
            ),
            CriterionSeed(
                "inclusion",
                platelet_quote,
                "laboratory",
                "platelet count",
                "greater_than_or_equal",
                platelets,
                "x10^9/L",
                group_number=2,
            ),
        ],
        ("laboratory", "numeric_threshold", "inclusion"),
    )


def _and(case_number: int, variant: int) -> dict[str, Any]:
    diagnosis = (
        "melanoma",
        "lymphoma",
        "sarcoma",
        "glioma",
        "carcinoma",
        "leukemia",
        "mesothelioma",
        "myeloma",
    )[variant]
    first_quote = f"Histologically confirmed {diagnosis}"
    second_quote = "measurable disease"
    text = f"Inclusion Criteria:\n- {first_quote} and {second_quote}"
    return _fixture(
        case_number,
        FAMILIES[6],
        variant,
        text,
        [
            CriterionSeed(
                "inclusion",
                first_quote,
                "diagnosis",
                f"{diagnosis} diagnosis",
                "exists",
                True,
                connector="and",
            ),
            CriterionSeed(
                "inclusion",
                second_quote,
                "disease_state",
                "measurable disease",
                "exists",
                True,
                connector="and",
            ),
        ],
        ("logic_and", "multi_clause", "one_bullet_multiple_labels", "inclusion"),
    )


def _or(case_number: int, variant: int) -> dict[str, Any]:
    first_quote, second_quote = (
        ("documented asthma", "chronic obstructive pulmonary disease"),
        ("type 1 diabetes", "type 2 diabetes"),
        ("ulcerative colitis", "Crohn disease"),
        ("migraine with aura", "migraine without aura"),
        ("rheumatoid arthritis", "psoriatic arthritis"),
        ("systolic heart failure", "diastolic heart failure"),
        ("acute leukemia", "chronic leukemia"),
        ("localized disease", "metastatic disease"),
    )[variant]
    text = f"Inclusion Criteria:\n- {first_quote} or {second_quote}"
    return _fixture(
        case_number,
        FAMILIES[7],
        variant,
        text,
        [
            CriterionSeed(
                "inclusion",
                first_quote,
                "diagnosis",
                first_quote,
                "exists",
                True,
                connector="or",
            ),
            CriterionSeed(
                "inclusion",
                second_quote,
                "diagnosis",
                second_quote,
                "exists",
                True,
                connector="or",
            ),
        ],
        ("logic_or", "multi_clause", "one_bullet_multiple_labels", "inclusion"),
    )


def _range(case_number: int, variant: int) -> dict[str, Any]:
    lower, upper = (
        (18, 65),
        (21, 70),
        (25, 75),
        (30, 80),
        (35, 85),
        (40, 90),
        (45, 75),
        (50, 80),
    )[variant]
    quote = f"Age between {lower} and {upper} years"
    text = f"Inclusion Criteria:\n- {quote}"
    return _fixture(
        case_number,
        FAMILIES[8],
        variant,
        text,
        [
            CriterionSeed(
                "inclusion",
                quote,
                "age",
                "age",
                "between",
                [lower, upper],
                "years",
            )
        ],
        ("range", "between", "numeric_threshold", "inclusion"),
    )


def _punctuation(case_number: int, variant: int) -> dict[str, Any]:
    quote = (
        "Histologically confirmed lymphoma",
        "Measurable disease on imaging",
        "Available baseline biopsy",
        "Stable outpatient condition",
        "Documented tumor diagnosis",
        "Completed screening assessment",
        "Suitable archival sample",
        "Confirmed target condition",
    )[variant]
    decorated = (
        f"- {quote}.",
        f"* {quote};",
        f"• {quote}",
        f"1. {quote}.",
        f"2) ({quote})",
        f"- {quote} — documented",
        f"- Eligibility: {quote}.",
        f'- "{quote}".',
    )[variant]
    category = (
        "diagnosis",
        "disease_state",
        "procedure",
        "disease_state",
        "diagnosis",
        "procedure",
        "procedure",
        "diagnosis",
    )[variant]
    text = f"Key Inclusion Criteria:\n{decorated}"
    return _fixture(
        case_number,
        FAMILIES[9],
        variant,
        text,
        [CriterionSeed("inclusion", quote, category, quote.casefold(), "exists", True)],
        ("punctuation", "evidence_span", "format_variation", "inclusion"),
    )


_BUILDERS = (
    _simple,
    _numeric,
    _temporal,
    _negation,
    _demographics_consent,
    _laboratory,
    _and,
    _or,
    _range,
    _punctuation,
)


def generate_cases() -> list[dict[str, Any]]:
    """Return all cases in stable family/variant order."""

    cases: list[dict[str, Any]] = []
    case_number = 1
    for builder in _BUILDERS:
        for variant in range(VARIANTS_PER_FAMILY):
            cases.append(builder(case_number, variant))
            case_number += 1
    if len(cases) != CASE_COUNT:
        raise AssertionError(f"expected {CASE_COUNT} cases, generated {len(cases)}")
    return cases


def _json_bytes(payload: object) -> bytes:
    return (json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=True) + "\n").encode()


def build_manifest(case_bytes: list[tuple[str, bytes, dict[str, Any]]]) -> dict[str, Any]:
    """Build the manifest from the exact rendered fixture bytes."""

    slice_counts: Counter[str] = Counter()
    family_counts: Counter[str] = Counter()
    records: list[dict[str, Any]] = []
    for path, raw, case in case_bytes:
        provenance = case["provenance"]
        family = str(provenance["family"])
        slices = str(provenance["slices"]).split(",")
        family_counts[family] += 1
        slice_counts.update(slices)
        records.append(
            {
                "path": path,
                "sha256": hashlib.sha256(raw).hexdigest(),
                "family": family,
                "slices": slices,
                "has_reference": True,
            }
        )
    return {
        "dataset_version": DATASET_VERSION,
        "case_count": CASE_COUNT,
        "family_count": len(FAMILIES),
        "variants_per_family": VARIANTS_PER_FAMILY,
        "family_counts": dict(sorted(family_counts.items())),
        "slice_counts": dict(sorted(slice_counts.items())),
        "annotation": {
            "authoring_status": "single_author",
            "method": "deterministic_templates",
            "review_status": "independent_review_pending",
        },
        "clinical_validation": False,
        "license": "MIT",
        "records": records,
    }


def write_dataset(output_dir: Path = DEFAULT_OUTPUT_DIR) -> dict[str, Any]:
    """Write byte-stable fixtures and their manifest to ``output_dir``."""

    output_dir.mkdir(parents=True, exist_ok=True)
    rendered: list[tuple[str, bytes, dict[str, Any]]] = []
    for case_number, case in enumerate(generate_cases(), start=1):
        filename = f"case_{case_number:03d}.json"
        raw = _json_bytes(case)
        (output_dir / filename).write_bytes(raw)
        rendered.append((filename, raw, case))
    manifest = build_manifest(rendered)
    (output_dir / "manifest.json").write_bytes(_json_bytes(manifest))
    return manifest


if __name__ == "__main__":
    write_dataset()
