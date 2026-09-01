"""Sequential orchestration for the network-free evaluation suite."""

from __future__ import annotations

from pathlib import Path

from criteriabench.evaluation.metrics import evaluate_extraction
from criteriabench.suite.analysis import classify_errors, count_exact_true_positives
from criteriabench.suite.baselines import create_baseline
from criteriabench.suite.loader import load_suite
from criteriabench.suite.models import (
    SUITE_VERSION,
    BaselineName,
    CaseEvaluation,
    CaseLineage,
    DatasetCard,
    ExampleResult,
    LoadedSuite,
    SuiteReport,
)
from criteriabench.suite.statistics import (
    ANALYSIS_CONTRACT_SHA256,
    compare_paired,
    summarize_baseline,
)

LIMITATIONS = [
    "All 80 cases are generated from 10 parametric synthetic templates, not sampled records.",
    "The AI-assisted deterministic references were produced in a single-author workflow.",
    "Labels lack independent second-human and clinical-domain review and adjudication.",
    "The suite is not clinical validation and must not support clinical decisions.",
    "The offline baselines make no LLM calls, so their scores do not measure LLM quality.",
    "Sensitivity intervals describe only this fixed case mix and are not population estimates.",
    (
        "Fixed-suite sensitivities do not measure model stochasticity; both baselines are "
        "deterministic."
    ),
]
AUTHORING_DISCLOSURE = (
    "AI-assisted deterministic templates; independent second-human and clinical-domain "
    "review/adjudication pending."
)
REPRODUCIBILITY_COMMAND = (
    "uv run --frozen --no-env-file criteriabench-suite data/synthetic_v0_1/manifest.json "
    "--configs empty-v1 rules-v1 "
    "--markdown-output artifacts/synthetic-v0.1.1.md "
    "--json-output artifacts/synthetic-v0.1.1.json "
    "--check-json docs/results/synthetic-v0.1.1.json "
    "--check-markdown docs/results/synthetic-v0.1.1.md"
)
EXAMPLE_FAMILIES = (
    "simple_inclusion_exclusion",
    "numeric_thresholds",
    "temporal_constraints",
    "and_multi_clause",
    "or_multi_clause",
)


def _derive_case_lineage(loaded: LoadedSuite) -> list[CaseLineage]:
    """Derive explicit IDs from frozen manifest order without changing manifest bytes."""

    family_positions: dict[str, int] = {}
    lineage: list[CaseLineage] = []
    for loaded_case in loaded.cases:
        family_id = loaded_case.family
        variant_index = family_positions.get(family_id, 0) + 1
        family_positions[family_id] = variant_index
        base_template_id = f"{family_id}-template-001"
        lineage.append(
            CaseLineage(
                trial_id=loaded_case.fixture.trial.trial_id,
                family_id=family_id,
                base_template_id=base_template_id,
                variant_id=f"{base_template_id}-variant-{variant_index:03d}",
            )
        )
    if family_positions != loaded.manifest.family_counts:
        raise ValueError("derived lineage does not match manifest family counts")
    if len({item.base_template_id for item in lineage}) != loaded.manifest.family_count:
        raise ValueError("derived base-template count does not match manifest")
    return lineage


async def run_suite(manifest_path: Path, configs: tuple[BaselineName, ...]) -> SuiteReport:
    """Load, evaluate, and summarize allowlisted offline baselines in stable order."""

    if not configs or len(set(configs)) != len(configs):
        raise ValueError("configs must be a non-empty unique allowlisted sequence")
    loaded = load_suite(manifest_path)
    results = await evaluate_baselines(loaded, configs)
    summaries = [summarize_baseline(results[config]) for config in configs]
    comparisons = []
    if "empty-v1" in results and "rules-v1" in results:
        comparisons.append(compare_paired(results["rules-v1"], results["empty-v1"]))
    return SuiteReport(
        suite_version=SUITE_VERSION,
        analysis_contract_sha256=ANALYSIS_CONTRACT_SHA256,
        dataset=_dataset_card(loaded),
        baselines=summaries,
        paired_comparisons=comparisons,
        examples=_select_examples(loaded, results, configs),
        reproducibility_command=REPRODUCIBILITY_COMMAND,
        limitations=LIMITATIONS,
    )


async def evaluate_baselines(
    loaded: LoadedSuite,
    configs: tuple[BaselineName, ...],
) -> dict[BaselineName, list[CaseEvaluation]]:
    results: dict[BaselineName, list[CaseEvaluation]] = {}
    lineage_by_trial = {item.trial_id: item for item in _derive_case_lineage(loaded)}
    for config in configs:
        baseline = create_baseline(config)
        case_results: list[CaseEvaluation] = []
        for loaded_case in loaded.cases:
            fixture = loaded_case.fixture
            lineage = lineage_by_trial[fixture.trial.trial_id]
            prediction = await baseline.predict(fixture.trial)
            if prediction.trial_id != fixture.trial.trial_id:
                raise ValueError(f"baseline returned the wrong trial_id: {config}")
            reference = fixture.reference
            case_results.append(
                CaseEvaluation(
                    config=config,
                    trial_id=fixture.trial.trial_id,
                    family=loaded_case.family,
                    base_template_id=lineage.base_template_id,
                    variant_id=lineage.variant_id,
                    slices=loaded_case.slices,
                    reference_nonempty=bool(
                        reference.inclusion_criteria or reference.exclusion_criteria
                    ),
                    completed=True,
                    schema_valid=True,
                    exact_true_positives=count_exact_true_positives(prediction, reference),
                    prediction=prediction,
                    evaluation=evaluate_extraction(prediction, reference),
                    errors=classify_errors(prediction, reference),
                )
            )
        results[config] = case_results
    return results


def _dataset_card(loaded: LoadedSuite) -> DatasetCard:
    manifest = loaded.manifest
    lineage = _derive_case_lineage(loaded)
    return DatasetCard(
        name="CriteriaBench Synthetic v0.1",
        version=manifest.dataset_version,
        manifest_sha256=loaded.manifest_sha256,
        case_count=manifest.case_count,
        family_count=manifest.family_count,
        variants_per_family=manifest.variants_per_family,
        family_counts=manifest.family_counts,
        slice_counts=manifest.slice_counts,
        base_template_count=len({item.base_template_id for item in lineage}),
        lineage_derivation="derived_from_manifest_family_and_record_order",
        lineage=lineage,
        license=manifest.license,
        annotation=manifest.annotation,
        authoring_disclosure=AUTHORING_DISCLOSURE,
        clinical_validation=manifest.clinical_validation,
    )


def _select_examples(
    loaded: LoadedSuite,
    results: dict[BaselineName, list[CaseEvaluation]],
    configs: tuple[BaselineName, ...],
) -> list[ExampleResult]:
    family_indexes = {
        loaded_case.family: index for index, loaded_case in reversed(list(enumerate(loaded.cases)))
    }
    try:
        indexes = [family_indexes[family] for family in EXAMPLE_FAMILIES]
    except KeyError as exc:
        raise ValueError("synthetic v0.1 is missing a required example family") from exc
    examples: list[ExampleResult] = []
    for index in indexes:
        loaded_case = loaded.cases[index]
        reference = loaded_case.fixture.reference
        evaluation = results[configs[0]][index]
        examples.append(
            ExampleResult(
                trial_id=loaded_case.fixture.trial.trial_id,
                family_id=evaluation.family,
                base_template_id=evaluation.base_template_id,
                variant_id=evaluation.variant_id,
                slices=loaded_case.slices,
                reference_criteria=len(reference.inclusion_criteria)
                + len(reference.exclusion_criteria),
                baseline_criterion_text_f1={
                    config: results[config][index].evaluation.exact_match_f1 for config in configs
                },
                baseline_mismatch_event_totals={
                    config: results[config][index].errors.total for config in configs
                },
            )
        )
    return examples
