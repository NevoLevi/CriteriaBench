"""Byte-stable JSON and Markdown rendering for suite reports."""

from __future__ import annotations

import json

from criteriabench.suite.models import ConfidenceInterval, MetricAggregate, SuiteReport


def render_json(report: SuiteReport) -> bytes:
    payload = report.model_dump(mode="json")
    return (
        json.dumps(payload, ensure_ascii=True, indent=2, sort_keys=True, allow_nan=False) + "\n"
    ).encode("utf-8")


def render_markdown(report: SuiteReport) -> bytes:
    lines = [
        "# CriteriaBench offline suite report",
        "",
        f"- Suite: `{report.suite_version}`",
        f"- Analysis contract SHA-256: `{report.analysis_contract_sha256}`",
        f"- Dataset manifest SHA-256: `{report.dataset.manifest_sha256}`",
        "",
        "## Dataset card",
        "",
        f"- Name/version: {report.dataset.name} (`{report.dataset.version}`)",
        f"- Cases: {report.dataset.case_count}",
        f"- Families: {report.dataset.family_count} "
        f"({report.dataset.variants_per_family} variants each)",
        f"- License: {report.dataset.license}",
        "- Annotation: single-author deterministic templates; independent review pending",
        "- Clinical validation: no",
        "",
        "### Family counts",
        "",
        "| Family | Cases |",
        "|---|---:|",
    ]
    lines.extend(
        f"| {family} | {count} |" for family, count in report.dataset.family_counts.items()
    )
    lines.extend(["", "### Slice counts", "", "| Slice | Cases |", "|---|---:|"])
    lines.extend(f"| {name} | {count} |" for name, count in report.dataset.slice_counts.items())
    lines.extend(
        [
            "",
            "## Baselines",
            "",
            "Both baselines are paid=false, network=false, input_tokens=0, output_tokens=0, "
            "estimated_cost_usd=0.",
            "",
            "| Baseline | Complete | Schema | Paid | Network | Tokens in/out | Cost USD | "
            "Micro exact F1 | Mean exact F1 (95% CI) | Mean token F1 (95% CI) | "
            "Mean macro field accuracy (95% CI) | Trial-perfect |",
            "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
        ]
    )
    for baseline in report.baselines:
        all_cases = baseline.all_cases
        intervals = baseline.mean_metric_intervals
        lines.append(
            f"| {baseline.config} | {_pct(baseline.completion_rate)} | "
            f"{_pct(baseline.schema_valid_rate)} | {str(baseline.paid).lower()} | "
            f"{str(baseline.network).lower()} | {baseline.input_tokens}/{baseline.output_tokens} | "
            f"{_f(baseline.estimated_cost_usd)} | {_f(all_cases.micro_exact_f1)} | "
            f"{_ci(intervals['mean_exact_f1'])} | {_ci(intervals['mean_token_f1'])} | "
            f"{_ci(intervals['mean_macro_field_accuracy'])} | "
            f"{_pct(all_cases.trial_perfect_rate)} |"
        )
    lines.extend(
        [
            "",
            "### All versus nonempty-gold cases",
            "",
            "| Baseline | Cohort | Cases | Exact TP / predicted / gold | Micro P / R / F1 | "
            "Mean exact / token / macro |",
            "|---|---|---:|---:|---:|---:|",
        ]
    )
    for baseline in report.baselines:
        lines.append(_aggregate_row(baseline.config, "all", baseline.all_cases))
        lines.append(_aggregate_row(baseline.config, "nonempty gold", baseline.nonempty_gold_cases))
    lines.extend(
        [
            "",
            "## Paired bootstrap comparisons",
            "",
            "Paired percentile bootstrap: 10,000 resamples, seed 20260901, 95% intervals.",
            "",
            "| Challenger - reference | Metric | Mean delta (95% CI) |",
            "|---|---|---:|",
        ]
    )
    if report.paired_comparisons:
        for comparison in report.paired_comparisons:
            label = f"{comparison.challenger} - {comparison.reference}"
            for metric, interval in comparison.delta_intervals.items():
                lines.append(f"| {label} | {metric} | {_ci(interval)} |")
            lines.extend(["", f"> Limitation: {comparison.limitation}", ""])
    else:
        lines.append("| n/a | n/a | n/a |")
    lines.extend(
        [
            "## Per-slice results",
            "",
            "| Baseline | Slice | Cases | Micro exact F1 | Mean exact F1 | Mean token F1 | "
            "Mean macro field accuracy |",
            "|---|---|---:|---:|---:|---:|---:|",
        ]
    )
    for baseline in report.baselines:
        for slice_name, aggregate in baseline.per_slice.items():
            lines.append(
                f"| {baseline.config} | {slice_name} | {aggregate.case_count} | "
                f"{_f(aggregate.micro_exact_f1)} | {_f(aggregate.mean_exact_f1)} | "
                f"{_f(aggregate.mean_token_f1)} | "
                f"{_f(aggregate.mean_macro_field_accuracy)} |"
            )
    lines.extend(
        [
            "",
            "## Error taxonomy",
            "",
            "Counts use the same deterministic optimal alignment as the evaluator.",
            "",
            "| Baseline | Error type | Count |",
            "|---|---|---:|",
        ]
    )
    for baseline in report.baselines:
        for error_type, count in baseline.taxonomy.model_dump().items():
            lines.append(f"| {baseline.config} | {error_type} | {count} |")
    lines.extend(
        [
            "",
            "## Five deterministic examples",
            "",
            "| Trial | Family | Slices | Gold criteria | Baseline exact F1 | Error totals |",
            "|---|---|---|---:|---|---|",
        ]
    )
    for example in report.examples:
        scores = ", ".join(
            f"{name}={_f(value)}" for name, value in example.baseline_exact_f1.items()
        )
        errors = ", ".join(
            f"{name}={value}" for name, value in example.baseline_error_totals.items()
        )
        lines.append(
            f"| {example.trial_id} | {example.family} | {', '.join(example.slices)} | "
            f"{example.reference_criteria} | {scores} | {errors} |"
        )
    lines.extend(
        [
            "",
            "## Reproduce",
            "",
            "```console",
            report.reproducibility_command,
            "```",
            "",
            "## Limitations",
            "",
        ]
    )
    lines.extend(f"- {limitation}" for limitation in report.limitations)
    return ("\n".join(lines) + "\n").encode("utf-8")


def _aggregate_row(config: str, cohort: str, aggregate: MetricAggregate) -> str:
    counts = (
        f"{aggregate.exact_true_positives} / {aggregate.predicted_criteria} / "
        f"{aggregate.reference_criteria}"
    )
    micro = (
        f"{_f(aggregate.micro_exact_precision)} / {_f(aggregate.micro_exact_recall)} / "
        f"{_f(aggregate.micro_exact_f1)}"
    )
    means = (
        f"{_f(aggregate.mean_exact_f1)} / {_f(aggregate.mean_token_f1)} / "
        f"{_f(aggregate.mean_macro_field_accuracy)}"
    )
    return f"| {config} | {cohort} | {aggregate.case_count} | {counts} | {micro} | {means} |"


def _ci(interval: ConfidenceInterval) -> str:
    return f"{_f(interval.estimate)} [{_f(interval.low)}, {_f(interval.high)}]"


def _f(value: float) -> str:
    return f"{value:.6f}"


def _pct(value: float) -> str:
    return f"{100.0 * value:.2f}%"
