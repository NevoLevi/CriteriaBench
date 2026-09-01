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
    "All 80 cases are constructed synthetic templates, not sampled clinical-trial records.",
    "The reference labels were authored by one person through deterministic templates.",
    "The labels have not received independent review or adjudication.",
    "The suite is not clinical validation and must not support clinical decisions.",
    "The offline baselines make no LLM calls, so their scores do not measure LLM quality.",
    "Results characterize only this fixed case mix and do not establish external validity.",
]
REPRODUCIBILITY_COMMAND = (
    "criteriabench-suite data/synthetic_v0_1/manifest.json "
    "--configs empty-v1 rules-v1 --json-output suite-results.json "
    "--markdown-output suite-results.md"
)
EXAMPLE_FAMILIES = (
    "simple_inclusion_exclusion",
    "numeric_thresholds",
    "temporal_constraints",
    "and_multi_clause",
    "or_multi_clause",
)


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
    for config in configs:
        baseline = create_baseline(config)
        case_results: list[CaseEvaluation] = []
        for loaded_case in loaded.cases:
            fixture = loaded_case.fixture
            prediction = await baseline.predict(fixture.trial)
            if prediction.trial_id != fixture.trial.trial_id:
                raise ValueError(f"baseline returned the wrong trial_id: {config}")
            reference = fixture.reference
            case_results.append(
                CaseEvaluation(
                    config=config,
                    trial_id=fixture.trial.trial_id,
                    family=loaded_case.family,
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
    return DatasetCard(
        name="CriteriaBench Synthetic v0.1",
        version=manifest.dataset_version,
        manifest_sha256=loaded.manifest_sha256,
        case_count=manifest.case_count,
        family_count=manifest.family_count,
        variants_per_family=manifest.variants_per_family,
        family_counts=manifest.family_counts,
        slice_counts=manifest.slice_counts,
        license=manifest.license,
        annotation=manifest.annotation,
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
        examples.append(
            ExampleResult(
                trial_id=loaded_case.fixture.trial.trial_id,
                family=loaded_case.family,
                slices=loaded_case.slices,
                reference_criteria=len(reference.inclusion_criteria)
                + len(reference.exclusion_criteria),
                baseline_exact_f1={
                    config: results[config][index].evaluation.exact_match_f1 for config in configs
                },
                baseline_error_totals={
                    config: results[config][index].errors.total for config in configs
                },
            )
        )
    return examples
