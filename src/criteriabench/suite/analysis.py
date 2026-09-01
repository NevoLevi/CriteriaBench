"""Evaluator-aligned deterministic error analysis."""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable

import criteriabench.evaluation.metrics as evaluator
from criteriabench.domain.schemas import ClinicalTrialEligibility
from criteriabench.suite.models import ErrorTaxonomy

TAXONOMY_FIELDS = tuple(ErrorTaxonomy.model_fields)


def count_exact_true_positives(
    predicted: ClinicalTrialEligibility,
    reference: ClinicalTrialEligibility,
) -> int:
    """Count the evaluator's exact criterion-key multiset intersection directly."""

    predicted_items = predicted.inclusion_criteria + predicted.exclusion_criteria
    reference_items = reference.inclusion_criteria + reference.exclusion_criteria
    predicted_keys = Counter(evaluator._criterion_key(item) for item in predicted_items)
    reference_keys = Counter(evaluator._criterion_key(item) for item in reference_items)
    return sum((predicted_keys & reference_keys).values())


def classify_errors(
    predicted: ClinicalTrialEligibility,
    reference: ClinicalTrialEligibility,
) -> ErrorTaxonomy:
    """Classify errors using the evaluator's frozen optimal alignment.

    Importing the evaluator's alignment deliberately avoids a second matcher whose
    pair choices could disagree with ``evaluate_extraction``.
    """

    predicted_items = predicted.inclusion_criteria + predicted.exclusion_criteria
    reference_items = reference.inclusion_criteria + reference.exclusion_criteria
    pairs = evaluator._align(predicted_items, reference_items)
    paired_predicted = {id(pair.predicted) for pair in pairs}
    paired_reference = {id(pair.reference) for pair in pairs}
    counts = {field: 0 for field in TAXONOMY_FIELDS}
    counts["spurious_criterion"] = sum(id(item) not in paired_predicted for item in predicted_items)
    counts["missing_criterion"] = sum(id(item) not in paired_reference for item in reference_items)

    for pair in pairs:
        predicted_item = pair.predicted
        reference_item = pair.reference
        if evaluator._normalize(predicted_item.normalized_text) != evaluator._normalize(
            reference_item.normalized_text
        ):
            counts["text_mismatch"] += 1
        for field in ("category", "concept", "operator", "value", "unit"):
            if getattr(predicted_item, field) != getattr(reference_item, field):
                counts[f"{field}_mismatch"] += 1
        if predicted_item.negated != reference_item.negated:
            counts["negation_mismatch"] += 1

        predicted_temporal = predicted_item.temporal_constraint
        reference_temporal = reference_item.temporal_constraint
        for field in ("relation", "quantity", "unit", "reference_event", "raw_text"):
            if getattr(predicted_temporal, field) != getattr(reference_temporal, field):
                counts[f"temporal_{field}_mismatch"] += 1

        if predicted_item.logic_group.connector != reference_item.logic_group.connector:
            counts["logic_connector_mismatch"] += 1
        if predicted_item.logic_group.parent_group_id != reference_item.logic_group.parent_group_id:
            counts["logic_parent_mismatch"] += 1
        if predicted_item.evidence.quote != reference_item.evidence.quote:
            counts["evidence_quote_mismatch"] += 1
        predicted_offsets = (predicted_item.evidence.start_char, predicted_item.evidence.end_char)
        reference_offsets = (reference_item.evidence.start_char, reference_item.evidence.end_char)
        if predicted_offsets != reference_offsets:
            counts["evidence_offset_mismatch"] += 1
    return ErrorTaxonomy.model_validate(counts)


def sum_taxonomies(taxonomies: Iterable[ErrorTaxonomy]) -> ErrorTaxonomy:
    totals = {field: 0 for field in TAXONOMY_FIELDS}
    for taxonomy in taxonomies:
        for field, count in taxonomy.model_dump().items():
            totals[field] += count
    return ErrorTaxonomy.model_validate(totals)
