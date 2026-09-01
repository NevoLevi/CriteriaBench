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
        f"- Derived base templates: {report.dataset.base_template_count}",
        f"- License: {report.dataset.license}",
        f"- Authoring disclosure: {report.dataset.authoring_disclosure}",
        "- Clinical validation: no",
        "",
        "## Metric interpretation",
        "",
        "Exact criterion-text F1 matches criterion kind plus evaluator-normalized text; it is "
        "not exact equality of the full structured object.",
        "Agreement on the eight evaluated structured fields is reported separately.",
        "",
        "### Family counts",
        "",
        "| Family | Cases |",
        "|---|---:|",
    ]
    lines.extend(
        f"| {family} | {count} |" for family, count in report.dataset.family_counts.items()
    )
    lines.extend(
        [
            "",
            "### Derived family/template/variant lineage",
            "",
            "Lineage is derived from frozen manifest family and record order; fixture and "
            "manifest bytes are unchanged.",
            "",
            "| Family ID | Base template ID | Variant IDs |",
            "|---|---|---|",
        ]
    )
    for family in report.dataset.family_counts:
        members = [item for item in report.dataset.lineage if item.family_id == family]
        base_templates = ", ".join(sorted({item.base_template_id for item in members}))
        variants = ", ".join(item.variant_id for item in members)
        lines.append(f"| {family} | {base_templates} | {variants} |")
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
            "Micro criterion-text F1 | Mean criterion-text F1 "
            "(95% case-resampling sensitivity) | Mean token F1 (95% case-resampling sensitivity) | "
            "Mean macro field accuracy (95% case-resampling sensitivity) | "
            "Criterion-text-perfect |",
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
            f"{_f(baseline.estimated_cost_usd)} | {_f(all_cases.micro_criterion_text_f1)} | "
            f"{_ci(intervals['mean_criterion_text_f1'])} | {_ci(intervals['mean_token_f1'])} | "
            f"{_ci(intervals['mean_macro_field_accuracy'])} | "
            f"{_pct(all_cases.criterion_text_perfect_trial_rate)} |"
        )
    lines.extend(
        [
            "",
            "### All versus nonempty-reference cases",
            "",
            "| Baseline | Cohort | Cases | Criterion-text TP / FP / FN | Micro P / R / F1 | "
            "Mean criterion-text / token / macro |",
            "|---|---|---:|---:|---:|---:|",
        ]
    )
    for baseline in report.baselines:
        lines.append(_aggregate_row(baseline.config, "all", baseline.all_cases))
        lines.append(
            _aggregate_row(
                baseline.config,
                "nonempty reference",
                baseline.nonempty_reference_cases,
            )
        )
    lines.extend(
        [
            "",
            "### All eight structured-field accuracies",
            "",
            "| Baseline | Category | Concept | Operator | Value | Unit | Negated | "
            "Temporal relation | Logic connector |",
            "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
        ]
    )
    for baseline in report.baselines:
        fields = baseline.all_cases.mean_field_accuracies
        lines.append(
            f"| {baseline.config} | {_f(fields.category)} | {_f(fields.concept)} | "
            f"{_f(fields.operator)} | {_f(fields.value)} | {_f(fields.unit)} | "
            f"{_f(fields.negated)} | {_f(fields.temporal_relation)} | "
            f"{_f(fields.logic_connector)} |"
        )
    rules_baseline = next(
        (baseline for baseline in report.baselines if baseline.config == "rules-v1"), None
    )
    if rules_baseline is not None:
        rules_all = rules_baseline.all_cases
        lines.extend(
            [
                "",
                "### Interpretation boundary",
                "",
                "On this fixed constructed suite, `rules-v1` is stronger at recovering or "
                "overlapping criterion text than at reproducing the eight evaluated structured "
                f"fields: mean exact criterion-text F1 {_f(rules_all.mean_criterion_text_f1)}, "
                f"mean token-overlap F1 {_f(rules_all.mean_token_f1)}, and mean structured-field "
                f"macro accuracy {_f(rules_all.mean_macro_field_accuracy)}. These differently "
                "aggregated metrics are descriptive engineering evidence, not proof of semantic "
                "understanding.",
                "",
                "The zero exact criterion-text scores for the `logic_and`, `logic_or`, and "
                "`multi_clause` slices reflect the known segmentation/grouping mismatch: "
                "`rules-v1` emits one criterion for a source bullet whose reference contains two "
                "grouped criteria. They do not by themselves prove a general reasoning "
                "limitation.",
            ]
        )
    lines.extend(
        [
            "",
            "### Per-family results",
            "",
            "| Baseline | Family | Cases | Criterion-text TP / FP / FN | Micro P / R / F1 | "
            "Mean criterion-text / token / macro |",
            "|---|---|---:|---:|---:|---:|",
        ]
    )
    for baseline in report.baselines:
        for family, aggregate in baseline.per_family.items():
            lines.append(_aggregate_row(baseline.config, family, aggregate))
    lines.extend(
        [
            "",
            "### Leave-one-family-out sensitivity",
            "",
            "| Baseline | Excluded family | Cases | Criterion-text TP / FP / FN | "
            "Micro P / R / F1 | Mean criterion-text / token / macro |",
            "|---|---|---:|---:|---:|---:|",
        ]
    )
    for baseline in report.baselines:
        for family, aggregate in baseline.leave_one_family_out.items():
            lines.append(_aggregate_row(baseline.config, family, aggregate))
    lines.extend(
        [
            "",
            "### 10-family-cluster baseline sensitivity",
            "",
            "These are fixed-suite sensitivity intervals, not population confidence intervals.",
            "",
            "| Baseline | Metric | Mean (95% family-cluster sensitivity) |",
            "|---|---|---:|",
        ]
    )
    for baseline in report.baselines:
        for metric, interval in baseline.family_cluster_mean_metric_intervals.items():
            lines.append(f"| {baseline.config} | {metric} | {_ci(interval)} |")
    lines.extend(
        [
            "",
            "## Paired fixed-suite sensitivity comparisons",
            "",
            "Case and whole-family percentile resampling: 10,000 draws, seed 20260901.",
            "",
            "| Challenger - reference | Resampling unit | Metric | "
            "Mean delta (95% resampling sensitivity) |",
            "|---|---|---|---:|",
        ]
    )
    if report.paired_comparisons:
        for comparison in report.paired_comparisons:
            label = f"{comparison.challenger} - {comparison.reference}"
            for metric, interval in comparison.delta_intervals.items():
                lines.append(f"| {label} | case | {metric} | {_ci(interval)} |")
            for metric, interval in comparison.family_cluster_delta_intervals.items():
                lines.append(f"| {label} | family (10 clusters) | {metric} | {_ci(interval)} |")
            lines.extend(["", f"> Limitation: {comparison.limitation}", ""])
    else:
        lines.append("| n/a | n/a | n/a |")
    lines.extend(
        [
            "## Per-slice results",
            "",
            "| Baseline | Slice | Cases | Micro criterion-text F1 | Mean criterion-text F1 | "
            "Mean token F1 | Mean macro field accuracy |",
            "|---|---|---:|---:|---:|---:|---:|",
        ]
    )
    for baseline in report.baselines:
        for slice_name, aggregate in baseline.per_slice.items():
            lines.append(
                f"| {baseline.config} | {slice_name} | {aggregate.case_count} | "
                f"{_f(aggregate.micro_criterion_text_f1)} | "
                f"{_f(aggregate.mean_criterion_text_f1)} | "
                f"{_f(aggregate.mean_token_f1)} | "
                f"{_f(aggregate.mean_macro_field_accuracy)} |"
            )
    lines.extend(
        [
            "",
            "## Error taxonomy",
            "",
            "Counts use evaluator alignment and evaluator normalization for scored structured "
            "fields. Mismatch categories can overlap.",
            "",
            "| Baseline | Error type | Count | Denominator | Rate | Denominator basis |",
            "|---|---|---:|---:|---:|---|",
        ]
    )
    for baseline in report.baselines:
        for error_type, taxonomy_metric in baseline.taxonomy.metrics.items():
            lines.append(
                f"| {baseline.config} | {error_type} | {taxonomy_metric.count} | "
                f"{taxonomy_metric.denominator} | {_f(taxonomy_metric.rate)} | "
                f"{taxonomy_metric.denominator_basis} |"
            )
        lines.append(
            f"> {baseline.config}: aligned pairs={baseline.taxonomy.aligned_pairs}. "
            f"{baseline.taxonomy.overlap_note}"
        )
    lines.extend(
        [
            "",
            "## Five deterministic examples",
            "",
            "| Trial | Family | Base template | Variant | Slices | Reference criteria | "
            "Criterion-text F1 | Overlapping mismatch-event totals |",
            "|---|---|---|---|---|---:|---|---|",
        ]
    )
    for example in report.examples:
        scores = ", ".join(
            f"{name}={_f(value)}" for name, value in example.baseline_criterion_text_f1.items()
        )
        errors = ", ".join(
            f"{name}={value}" for name, value in example.baseline_mismatch_event_totals.items()
        )
        lines.append(
            f"| {example.trial_id} | {example.family_id} | {example.base_template_id} | "
            f"{example.variant_id} | {', '.join(example.slices)} | "
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
            "- This fixed constructed suite is engineering regression evidence, not a "
            "research-grade benchmark.",
        ]
    )
    lines.extend(f"- {limitation}" for limitation in report.limitations)
    return ("\n".join(lines) + "\n").encode("utf-8")


def _aggregate_row(config: str, cohort: str, aggregate: MetricAggregate) -> str:
    counts = (
        f"{aggregate.criterion_text_true_positives} / "
        f"{aggregate.criterion_text_false_positives} / "
        f"{aggregate.criterion_text_false_negatives}"
    )
    micro = (
        f"{_f(aggregate.micro_criterion_text_precision)} / "
        f"{_f(aggregate.micro_criterion_text_recall)} / "
        f"{_f(aggregate.micro_criterion_text_f1)}"
    )
    means = (
        f"{_f(aggregate.mean_criterion_text_f1)} / {_f(aggregate.mean_token_f1)} / "
        f"{_f(aggregate.mean_macro_field_accuracy)}"
    )
    return f"| {config} | {cohort} | {aggregate.case_count} | {counts} | {micro} | {means} |"


def _ci(interval: ConfidenceInterval) -> str:
    return f"{_f(interval.estimate)} [{_f(interval.low)}, {_f(interval.high)}]"


def _f(value: float) -> str:
    return f"{value:.6f}"


def _pct(value: float) -> str:
    return f"{100.0 * value:.2f}%"
