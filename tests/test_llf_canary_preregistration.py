from __future__ import annotations

import ast
import importlib
import json
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any, cast

import pytest

from criteriabench.real_eval.integrity import canonical_sha256
from criteriabench.real_eval.llf_binding import load_llf_generation_split
from criteriabench.real_eval.llf_canary_preregistration import (
    CanaryPreregistration,
    advancement_decision_bytes,
    build_llf_canary_preregistration,
    evaluate_canary_advancement,
    execution_binding_bytes,
    load_advancement_decision,
    load_execution_binding,
    load_live_plan,
    load_paid_authorization,
    load_preregistration,
    main,
    preregistration_bytes,
    verify_canary_advancement_decision,
    verify_canary_execution_binding,
)
from criteriabench.real_eval.llf_live_score import LlfLiveScoreReport, report_bytes
from criteriabench.real_live.contracts import LLF_WIRE_SCHEMA_SHA256
from criteriabench.real_live.planning import select_development_canary

ROOT = Path(__file__).resolve().parents[1]
LLF_DATA = ROOT / "data" / "real" / "llf"
COVERAGE_DIR = ROOT / "docs" / "results"
MODULE_PATH = ROOT / "src" / "criteriabench" / "real_eval" / "llf_canary_preregistration.py"
PUBLIC_PREREGISTRATION = COVERAGE_DIR / "llf-prompt-v1.1-canary-preregistration.json"


@pytest.fixture(scope="module")
def preregistration() -> CanaryPreregistration:
    return build_llf_canary_preregistration(
        dataset_dir=LLF_DATA,
        coverage_dir=COVERAGE_DIR,
    )


def test_real_canary_selection_and_bm25_numbers_are_frozen(
    preregistration: CanaryPreregistration,
) -> None:
    generation = load_llf_generation_split(LLF_DATA, "development")
    selected = select_development_canary(generation.cases)

    assert [case.case_id for case in preregistration.selection.cases] == [
        case.case_id for case in selected
    ]
    assert preregistration.selection.selected_case_set_sha256 == (
        "675c19d64172aa4d9545dbff2232664025bf8cd3aca45622f76f03cc0add432e"
    )
    assert preregistration.baseline.training_set_sha256 == (
        "e56d8f425b1ee140cb4ca10850f164697d16dc29fa6f85ce053b4b292aaf5e57"
    )
    assert preregistration.baseline.prediction_set_sha256 == (
        "839567dcc1d271dbe88cd3b61a9f2d41f6db9cd2ea865123d9bad21e40431920"
    )
    metrics = preregistration.baseline_metrics
    assert (metrics.exact_match_count, metrics.exact_match_accuracy) == (1, 0.04)
    assert metrics.primary_structure.model_dump() == {
        "true_positive": 153,
        "false_positive": 624,
        "false_negative": 578,
        "precision": 0.196911,
        "recall": 0.209302,
        "f1": 0.202918,
    }
    assert metrics.nodes.f1 == 0.259307
    assert metrics.edges.f1 == 0.142661
    assert metrics.typed_components.f1 == 0.259307


def test_preregistration_binds_inputs_code_contract_pricing_and_ambitious_gates(
    preregistration: CanaryPreregistration,
) -> None:
    assert preregistration.evidence_scope.model_dump() == {
        "uses_only_development_generation_split": True,
        "uses_only_development_references": True,
        "locked_test_references_opened": False,
        "model_or_provider_called": False,
        "network_used": False,
        "environment_or_secret_read": False,
        "locked_test_evidence": False,
        "claim": (
            "development-only preregistration and baseline; not an estimate of "
            "locked-test performance"
        ),
    }
    assert preregistration.generation_dataset.split == "development"
    assert preregistration.generation_dataset.case_count == 200
    assert preregistration.development_reference.semantic_case_count == 200
    assert preregistration.development_reference.missing_upstream_case_count == 0
    assert preregistration.planned_paid_call.output_contract.track == "llf_semantic_ast"
    assert preregistration.planned_paid_call.output_contract.schema_sha256 == LLF_WIRE_SCHEMA_SHA256
    assert preregistration.planned_paid_call.luna.model == "gpt-5.6-luna"
    assert preregistration.planned_paid_call.hard_budget_cap_usd == "0.170000000"
    assert preregistration.planned_paid_call.reserved_total_usd == "0.163840000"
    assert preregistration.planned_paid_call.maximum_attempts_per_case == 1
    engineering_limits = preregistration.implementation.engineering_limits
    assert engineering_limits.policy_id == "llf-live-engineering-limits-v1"
    assert engineering_limits.policy_sha256 == (
        "0ed21cce49d625018b0f26f5fb4b27667d8e9757e38755f7f17e8c4aee7dff52"
    )
    assert engineering_limits.model_dump(
        include={
            "logical_form_characters",
            "logical_form_utf8_bytes",
            "semantic_nodes",
            "semantic_depth",
            "call_arguments",
            "collection_items",
            "identifier_characters",
            "string_utf8_bytes",
        }
    ) == {
        "logical_form_characters": 8192,
        "logical_form_utf8_bytes": 16384,
        "semantic_nodes": 256,
        "semantic_depth": 64,
        "call_arguments": 32,
        "collection_items": 32,
        "identifier_characters": 128,
        "string_utf8_bytes": 1024,
    }
    assert engineering_limits.development_reference_statistics_used is False
    assert engineering_limits.locked_test_reference_statistics_used is False
    gates = preregistration.advancement_gates
    assert gates.decision_rule == "all_gates_must_pass"
    assert gates.required_attempted_count == gates.required_completed_count == 25
    assert gates.required_not_attempted_count == gates.required_failed_count == 0
    assert gates.required_usage_known_count == gates.required_response_id_count == 25
    assert gates.required_unique_response_id_count == 25
    assert gates.required_complete_timing_count == 25
    assert gates.required_external_attempt_claim_count == 25
    assert gates.required_unique_external_attempt_claim_count == 25
    assert gates.required_provider_service_tier == "default"
    assert gates.required_provider_service_tier_count == 25
    assert gates.required_usage_unknown_count == 0
    assert gates.minimum_primary_structure_f1 == 0.5
    assert gates.minimum_primary_structure_uplift_over_bm25 == 0.1
    assert gates.resulting_minimum_primary_structure_f1 == 0.5
    assert gates.minimum_exact_match_count == 2
    assert gates.maximum_p95_latency_ms == 60_000.0
    assert gates.sdk_retries == gates.app_retries == 0
    assert gates.on_any_failure == "do_not_authorize_or_run_locked_test"


def test_composition_is_aggregate_complete_and_reference_safe(
    preregistration: CanaryPreregistration,
) -> None:
    composition = preregistration.composition
    assert composition.criterion_kind_counts == {"inclusion": 9, "exclusion": 16}
    assert composition.source_length_bin_counts == {"short": 9, "medium": 8, "long": 8}
    assert composition.selection_stratum_counts == {
        "exclusion:long": 5,
        "exclusion:medium": 5,
        "exclusion:short": 6,
        "inclusion:long": 3,
        "inclusion:medium": 3,
        "inclusion:short": 3,
    }
    assert composition.reference_node_count_bins == {"1-5": 7, "6-10": 5, "11+": 13}
    assert composition.reference_edge_count_bins == {"0-4": 7, "5-9": 5, "10+": 13}
    document = json.loads(preregistration_bytes(preregistration))
    selected_rows = document["selection"]["cases"]
    assert all("source_text" not in row and "reference_sha256" not in row for row in selected_rows)


def test_builder_opens_no_locked_combined_environment_or_provider_input(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    opened: list[str] = []
    original_read_bytes = Path.read_bytes

    def guarded_read_bytes(path: Path) -> bytes:
        lowered = path.name.lower()
        opened.append(lowered)
        forbidden = {
            "test_references.jsonl",
            "records.jsonl",
            ".env",
            ".env.local",
        }
        if lowered in forbidden:
            raise AssertionError(f"forbidden preregistration read: {lowered}")
        return original_read_bytes(path)

    monkeypatch.setattr(Path, "read_bytes", guarded_read_bytes)
    build_llf_canary_preregistration(dataset_dir=LLF_DATA, coverage_dir=COVERAGE_DIR)

    assert "development_references.jsonl" in opened
    assert "llf-semantic-coverage-development.json" in opened
    assert "test_references.jsonl" not in opened
    assert "records.jsonl" not in opened
    tree = ast.parse(MODULE_PATH.read_text(encoding="utf-8"))
    imports = {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    } | {node.module or "" for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)}
    assert not imports.intersection({"openai", "httpx", "socket", "urllib", "requests"})


def test_canonical_round_trip_tamper_rejection_and_cli(
    preregistration: CanaryPreregistration,
    tmp_path: Path,
) -> None:
    assert load_preregistration(PUBLIC_PREREGISTRATION) == preregistration
    assert PUBLIC_PREREGISTRATION.read_bytes() == preregistration_bytes(preregistration)

    output = tmp_path / "preregistration.json"
    assert (
        main(
            [
                "build",
                "--dataset-dir",
                str(LLF_DATA),
                "--coverage-dir",
                str(COVERAGE_DIR),
                "--output",
                str(output),
            ]
        )
        == 0
    )
    assert output.read_bytes() == preregistration_bytes(preregistration)
    assert load_preregistration(output) == preregistration
    assert (
        main(
            [
                "check",
                "--dataset-dir",
                str(LLF_DATA),
                "--coverage-dir",
                str(COVERAGE_DIR),
                "--artifact",
                str(output),
            ]
        )
        == 0
    )

    canonical = output.read_bytes()
    output.write_bytes(
        json.dumps(json.loads(canonical), sort_keys=True, separators=(",", ":")).encode()
    )
    with pytest.raises(ValueError, match="not canonical public JSON"):
        load_preregistration(output)
    output.write_bytes(canonical)

    document = json.loads(output.read_bytes())
    document["advancement_gates"]["minimum_primary_structure_f1"] = 0.49
    output.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    with pytest.raises(ValueError):
        load_preregistration(output)


@pytest.mark.asyncio
async def test_gate_evaluator_passes_gold_mock_and_blocks_any_operational_failure(
    preregistration: CanaryPreregistration,
    tmp_path: Path,
) -> None:
    live_score_tests = importlib.import_module("tests.test_llf_live_score")
    sealed_canary = cast(
        Callable[..., Awaitable[Any]],
        vars(live_score_tests)["_sealed_canary"],
    )
    score_canary = cast(
        Callable[[Any], LlfLiveScoreReport],
        vars(live_score_tests)["_score"],
    )
    passing_chain = await sealed_canary(tmp_path / "passing")
    passing_report = score_canary(passing_chain)
    chain_preregistration = load_preregistration(passing_chain.preregistration_path)
    assert chain_preregistration == preregistration
    passing_plan, _ = load_live_plan(passing_chain.run_dir / "plan.json")
    passing_authorization, _ = load_paid_authorization(passing_chain.run_dir / "authorization.json")
    passing = evaluate_canary_advancement(
        chain_preregistration,
        passing_chain.execution_binding,
        passing_plan,
        passing_authorization,
        passing_report,
    )
    assert passing.advancement_status == "pass"
    assert passing.proceed_to_separate_locked_authorization is True
    assert all(check.passed for check in passing.checks)
    verify_canary_advancement_decision(
        chain_preregistration,
        passing_chain.execution_binding,
        passing_plan,
        passing_authorization,
        passing_report,
        passing,
    )

    cli_binding = tmp_path / "cli-execution-binding.json"
    assert (
        main(
            [
                "bind-execution",
                "--preregistration",
                str(passing_chain.preregistration_path),
                "--plan",
                str(passing_chain.run_dir / "plan.json"),
                "--intended-run-id",
                passing_chain.execution_binding.intended_run_id,
                "--intended-authorization-id",
                passing_chain.execution_binding.intended_authorization_id,
                "--host-output-directory-sha256",
                passing_chain.host_run_directory_sha256,
                "--authorization-state-directory-sha256",
                passing_chain.authorization_state_directory_sha256,
                "--output",
                str(cli_binding),
            ]
        )
        == 0
    )
    assert cli_binding.read_bytes() == execution_binding_bytes(passing_chain.execution_binding)
    assert (
        main(
            [
                "check-execution",
                "--preregistration",
                str(passing_chain.preregistration_path),
                "--plan",
                str(passing_chain.run_dir / "plan.json"),
                "--artifact",
                str(cli_binding),
            ]
        )
        == 0
    )

    report_path = tmp_path / "score-report.json"
    report_path.write_bytes(report_bytes(passing_report))
    decision_path = tmp_path / "advancement-decision.json"
    decision_args = [
        "--preregistration",
        str(passing_chain.preregistration_path),
        "--execution-binding",
        str(cli_binding),
        "--plan",
        str(passing_chain.run_dir / "plan.json"),
        "--authorization",
        str(passing_chain.run_dir / "authorization.json"),
        "--score-report",
        str(report_path),
    ]
    assert main(["decide", *decision_args, "--output", str(decision_path)]) == 0
    assert decision_path.read_bytes() == advancement_decision_bytes(passing)
    assert load_advancement_decision(decision_path) == passing
    assert main(["check-decision", *decision_args, "--artifact", str(decision_path)]) == 0

    forged_document = json.loads(cli_binding.read_bytes())
    forged_document["advancement_gates_sha256"] = "0" * 64
    forged_payload = dict(forged_document)
    forged_payload.pop("execution_binding_sha256")
    forged_document["execution_binding_sha256"] = canonical_sha256(forged_payload)
    forged_path = tmp_path / "self-sealed-forged-binding.json"
    forged_path.write_bytes(
        (
            json.dumps(
                forged_document,
                ensure_ascii=False,
                allow_nan=False,
                indent=2,
                sort_keys=True,
            )
            + "\n"
        ).encode("utf-8")
    )
    forged_binding = load_execution_binding(forged_path)
    with pytest.raises(ValueError, match="does not reproduce from the exact plan"):
        verify_canary_execution_binding(
            chain_preregistration,
            forged_binding,
            passing_plan,
            plan_artifact_sha256=passing_chain.execution_binding.plan_artifact_sha256,
        )

    failing_chain = await sealed_canary(tmp_path / "failing", fail_first=True)
    failing_report = score_canary(failing_chain)
    failing_preregistration = load_preregistration(failing_chain.preregistration_path)
    failing_plan, _ = load_live_plan(failing_chain.run_dir / "plan.json")
    failing_authorization, _ = load_paid_authorization(failing_chain.run_dir / "authorization.json")
    failing = evaluate_canary_advancement(
        failing_preregistration,
        failing_chain.execution_binding,
        failing_plan,
        failing_authorization,
        failing_report,
    )
    assert failing.advancement_status == "fail"
    assert failing.proceed_to_separate_locked_authorization is False
    assert {check.gate_id for check in failing.checks if not check.passed}.issuperset(
        {"complete_no_fatal_run", "usage_complete", "responses_provenance_complete"}
    )

    forged_pass_document = json.loads(advancement_decision_bytes(failing))
    for gate_check in forged_pass_document["checks"]:
        gate_check["passed"] = True
    forged_pass_document["advancement_status"] = "pass"
    forged_pass_document["proceed_to_separate_locked_authorization"] = True
    forged_pass_payload = dict(forged_pass_document)
    forged_pass_payload.pop("decision_sha256")
    forged_pass_document["decision_sha256"] = canonical_sha256(forged_pass_payload)
    forged_pass_path = tmp_path / "self-sealed-forged-pass-decision.json"
    forged_pass_path.write_bytes(
        (
            json.dumps(
                forged_pass_document,
                ensure_ascii=False,
                allow_nan=False,
                indent=2,
                sort_keys=True,
            )
            + "\n"
        ).encode("utf-8")
    )
    forged_pass = load_advancement_decision(forged_pass_path)
    assert forged_pass.advancement_status == "pass"
    with pytest.raises(ValueError, match="does not reproduce from the exact canary chain"):
        verify_canary_advancement_decision(
            failing_preregistration,
            failing_chain.execution_binding,
            failing_plan,
            failing_authorization,
            failing_report,
            forged_pass,
        )
