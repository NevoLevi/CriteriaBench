from __future__ import annotations

import ast
from pathlib import Path

import pytest

import criteriabench.suite_cli as suite_cli
from criteriabench.suite.reporting import render_json, render_markdown
from criteriabench.suite.runner import EXAMPLE_FAMILIES, run_suite
from criteriabench.suite.statistics import analysis_contract_sha256

PROJECT_ROOT = Path(__file__).resolve().parents[1]
MANIFEST = PROJECT_ROOT / "data" / "synthetic_v0_1" / "manifest.json"


async def test_full_report_is_stable_complete_and_explicitly_offline() -> None:
    report = await run_suite(MANIFEST, ("empty-v1", "rules-v1"))
    json_bytes = render_json(report)
    markdown_bytes = render_markdown(report)

    assert render_json(report) == json_bytes
    assert render_markdown(report) == markdown_bytes
    assert report.analysis_contract_sha256 == analysis_contract_sha256()
    assert len(report.analysis_contract_sha256) == 64
    assert report.dataset.case_count == 80
    assert [item.family for item in report.examples] == list(EXAMPLE_FAMILIES)
    assert len(report.paired_comparisons) == 1
    assert report.paired_comparisons[0].delta_intervals["mean_exact_f1"].resamples == 10_000

    for baseline in report.baselines:
        assert baseline.paid is False
        assert baseline.network is False
        assert baseline.input_tokens == baseline.output_tokens == 0
        assert baseline.estimated_cost_usd == 0.0
        assert baseline.completion_rate == baseline.schema_valid_rate == 1.0
        assert baseline.all_cases.case_count == 80
        assert baseline.nonempty_gold_cases.case_count == 80

    serialized = json_bytes.decode("utf-8")
    markdown = markdown_bytes.decode("utf-8")
    assert "manifest_path" not in serialized
    assert "C:\\Users\\" not in serialized
    assert '"paid": false' in serialized
    assert '"network": false' in serialized
    assert "single-author" in markdown
    assert "not clinical validation" in markdown
    assert "do not measure LLM quality" in markdown
    assert "one_bullet_multiple_labels" in markdown


def test_cli_surface_has_no_paid_or_network_mode_and_no_runtime_config_import() -> None:
    parser = suite_cli._parser()
    options = {option for action in parser._actions for option in action.option_strings}
    assert "--live" not in options
    assert "--acknowledge-paid-api" not in options
    assert "--budget-usd" not in options
    assert set(suite_cli.ALLOWED_BASELINES) == {"empty-v1", "rules-v1"}

    suite_root = Path(suite_cli.__file__).resolve().parent / "suite"
    sources = [Path(suite_cli.__file__).read_text(encoding="utf-8")]
    sources.extend(path.read_text(encoding="utf-8") for path in suite_root.glob("*.py"))
    imported_modules: set[str] = set()
    imported_names: set[str] = set()
    for source in sources:
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported_modules.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                if node.module is not None:
                    imported_modules.add(node.module)
                imported_names.update(alias.name for alias in node.names)
    assert "criteriabench.config" not in imported_modules
    assert "criteriabench.providers.openai" not in imported_modules
    assert "Settings" not in imported_names
    assert "OpenAIResponsesProvider" not in imported_names


def test_cli_paths_guard_overwrite_checks_and_environment_names(tmp_path: Path) -> None:
    json_output = tmp_path / "result.json"
    markdown_output = tmp_path / "result.md"
    check_json = tmp_path / "expected.json"
    suite_cli._validate_paths(
        MANIFEST,
        (json_output, markdown_output),
        (check_json, None),
        overwrite=False,
    )

    with pytest.raises(ValueError, match="check file"):
        suite_cli._validate_paths(
            MANIFEST,
            (check_json, markdown_output),
            (check_json, None),
            overwrite=True,
        )
    with pytest.raises(ValueError, match="environment-style"):
        suite_cli._validate_paths(
            MANIFEST,
            (json_output, markdown_output),
            (tmp_path / ".env.report", None),
            overwrite=False,
        )
    json_output.write_bytes(b"existing")
    with pytest.raises(ValueError, match="--overwrite"):
        suite_cli._validate_paths(
            MANIFEST,
            (json_output, markdown_output),
            (None, None),
            overwrite=False,
        )


def test_cli_atomic_outputs_and_exact_checks(tmp_path: Path) -> None:
    outputs = (tmp_path / "nested" / "report.json", tmp_path / "nested" / "report.md")
    payloads = (b'{"stable":true}\n', b"# stable\n")
    suite_cli._write_outputs_atomic(outputs, payloads)
    assert tuple(path.read_bytes() for path in outputs) == payloads
    assert not list(outputs[0].parent.glob(".*.tmp"))
    suite_cli._check_exact(outputs[0], payloads[0], "JSON")
    with pytest.raises(ValueError, match="generated bytes differ"):
        suite_cli._check_exact(outputs[0], b"different", "JSON")
