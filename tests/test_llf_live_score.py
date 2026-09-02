from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from functools import lru_cache
from pathlib import Path

import pytest
from pydantic import BaseModel

from criteriabench.real.llf_semantics import (
    LlfSemanticOutput,
    load_llf_scoring_references,
)
from criteriabench.real_eval.integrity import canonical_sha256
from criteriabench.real_eval.llf_binding import load_llf_generation_split
from criteriabench.real_eval.llf_canary_preregistration import (
    CanaryPreregistration,
    build_canary_execution_binding,
    build_llf_canary_preregistration,
    execution_binding_bytes,
    load_live_plan,
    load_paid_authorization,
    preregistration_bytes,
)
from criteriabench.real_eval.llf_live_score import (
    LlfLiveScoreError,
    LlfLiveScoreReport,
    main,
    report_bytes,
    score_live_llf_run,
)
from criteriabench.real_eval.models import GenerationCase
from criteriabench.real_live.contracts import (
    LLF_CANARY_ACKNOWLEDGEMENT,
    CanaryExecutionBinding,
    CaseOutcome,
    ReasoningEffort,
    SanitizedFailure,
    StrictOutputContract,
    UsageBreakdown,
    caller_execution_identity_sha256,
    frozen_execution_implementation,
    frozen_luna_configuration,
    llf_semantic_output_contract,
    price_usage,
    unavailable_usage,
)
from criteriabench.real_live.planning import (
    authorize_plan,
    build_llf_canary_plan,
    run_directory_sha256,
)
from criteriabench.real_live.runner import LiveRunError, recover_live_run, run_live_plan
from criteriabench.real_live.transport import (
    StructuredCallFailure,
    StructuredCallSuccess,
)

ROOT = Path(__file__).resolve().parents[1]
LLF_DATA = ROOT / "data" / "real" / "llf"
COVERAGE_DIR = ROOT / "docs" / "results"
NOW = "2026-09-02T12:00:00Z"
FIXED_NOW = datetime(2026, 9, 2, 13, 0, 0, tzinfo=UTC)
MEDIUM_FIXED_NOW = datetime(2026, 9, 2, 12, 10, 0, tzinfo=UTC)
TEST_IMAGE_ID = "sha256:" + "1" * 64
PROVIDER_MODEL = "gpt-5.6-luna"
PROVIDER_OBJECT = "response"
PROVIDER_SERVICE_TIER = "default"
RUNTIME_OUTPUT_DIRECTORY = "/run/artifacts/output"


def _known_usage() -> UsageBreakdown:
    costs = price_usage(
        uncached_input_tokens=7,
        cached_input_tokens=2,
        cache_write_input_tokens=1,
        output_tokens=3,
    )
    return UsageBreakdown(
        availability="complete",
        input_tokens=10,
        uncached_input_tokens=7,
        cached_input_tokens=2,
        cache_write_input_tokens=1,
        output_tokens=3,
        **costs,
    )


class GoldOrFailureCaller:
    def __init__(
        self,
        outputs: dict[str, LlfSemanticOutput],
        *,
        failed_case_id: str | None,
        reasoning_effort: ReasoningEffort = "none",
    ) -> None:
        self.outputs = outputs
        self.failed_case_id = failed_case_id
        self.reasoning_effort = reasoning_effort

    @property
    def execution_identity_sha256(self) -> str:
        return caller_execution_identity_sha256(
            frozen_luna_configuration(self.reasoning_effort),
            frozen_execution_implementation(),
        )

    async def call(
        self,
        case: GenerationCase,
        contract: StrictOutputContract[BaseModel],
    ) -> StructuredCallSuccess[BaseModel] | StructuredCallFailure:
        del contract
        if case.case_id == self.failed_case_id:
            return StructuredCallFailure(
                failure=SanitizedFailure(
                    kind="timeout",
                    retryable=False,
                    fingerprint_sha256=canonical_sha256(
                        {"test_failure": "timeout", "case_id": case.case_id}
                    ),
                ),
                response_id_sha256=None,
                usage=unavailable_usage(),
            )
        output = self.outputs[case.case_id]
        normalized = output.model_dump(mode="json")
        return StructuredCallSuccess(
            output=output,
            normalized_output=normalized,
            normalized_output_sha256=canonical_sha256(normalized),
            response_id_sha256=hashlib.sha256(f"response:{case.case_id}".encode()).hexdigest(),
            usage=_known_usage(),
            provider_model=PROVIDER_MODEL,
            provider_model_sha256=hashlib.sha256(PROVIDER_MODEL.encode()).hexdigest(),
            provider_response_object=PROVIDER_OBJECT,
            provider_response_object_sha256=hashlib.sha256(PROVIDER_OBJECT.encode()).hexdigest(),
            provider_service_tier=PROVIDER_SERVICE_TIER,
            provider_service_tier_sha256=hashlib.sha256(PROVIDER_SERVICE_TIER.encode()).hexdigest(),
        )


@lru_cache(maxsize=2)
def _preregistration(
    reasoning_effort: ReasoningEffort = "none",
) -> CanaryPreregistration:
    return build_llf_canary_preregistration(
        dataset_dir=LLF_DATA,
        coverage_dir=COVERAGE_DIR,
        reasoning_effort=reasoning_effort,
    )


def _compact_model_bytes(model: BaseModel) -> bytes:
    return (
        json.dumps(
            model.model_dump(mode="json"),
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode()


@dataclass(frozen=True, slots=True)
class SealedCanary:
    run_dir: Path
    authorization_state_dir: Path
    preregistration_path: Path
    execution_binding_path: Path
    execution_binding: CanaryExecutionBinding
    host_run_directory_sha256: str
    authorization_state_directory_sha256: str

    def score_kwargs(self) -> dict[str, object]:
        return {
            "run_dir": self.run_dir,
            "authorization_state_dir": self.authorization_state_dir,
            "preregistration_path": self.preregistration_path,
            "execution_binding_path": self.execution_binding_path,
            "host_run_directory_sha256": self.host_run_directory_sha256,
            "authorization_state_directory_sha256": (self.authorization_state_directory_sha256),
            "dataset_dir": LLF_DATA,
            "coverage_dir": COVERAGE_DIR,
        }


def _score(chain: SealedCanary) -> LlfLiveScoreReport:
    return score_live_llf_run(
        run_dir=chain.run_dir,
        authorization_state_dir=chain.authorization_state_dir,
        preregistration_path=chain.preregistration_path,
        execution_binding_path=chain.execution_binding_path,
        host_run_directory_sha256=chain.host_run_directory_sha256,
        authorization_state_directory_sha256=(chain.authorization_state_directory_sha256),
        dataset_dir=LLF_DATA,
        coverage_dir=COVERAGE_DIR,
    )


async def _sealed_canary(
    run_dir: Path,
    *,
    fail_first: bool = False,
    reasoning_effort: ReasoningEffort = "none",
) -> SealedCanary:
    generation = load_llf_generation_split(LLF_DATA, "development")
    contract = llf_semantic_output_contract()
    plan, selected = build_llf_canary_plan(
        generation.cases,
        dataset=generation.dataset,
        contract=contract,
        created_at_utc=NOW,
        runtime_image_id=TEST_IMAGE_ID,
        reasoning_effort=reasoning_effort,
    )
    references = load_llf_scoring_references(
        LLF_DATA / "development_references.jsonl",
        COVERAGE_DIR / "llf-semantic-coverage-development.json",
        split="development",
    )
    outputs = {
        reference.case_id: LlfSemanticOutput(
            root_node_id=reference.reference.root_node_id,
            nodes=reference.reference.nodes,
        )
        for reference in references.references
    }
    failed_case_id = selected[0].case_id if fail_first else None
    preregistration = _preregistration(reasoning_effort)
    public_dir = run_dir.parent / f"public-{run_dir.name}"
    public_dir.mkdir(parents=True)
    preregistration_path = public_dir / "llf-canary-preregistration.json"
    preregistration_path.write_bytes(preregistration_bytes(preregistration))
    authorization_state_dir = run_dir.parent / f"state-{run_dir.name}"
    authorization_state_dir.mkdir(parents=True)
    host_run_directory_sha256 = run_directory_sha256(run_dir)
    authorization_state_directory_sha256 = run_directory_sha256(authorization_state_dir)
    execution_binding = build_canary_execution_binding(
        preregistration=preregistration,
        plan=plan,
        plan_artifact_sha256=hashlib.sha256(_compact_model_bytes(plan)).hexdigest(),
        intended_run_id=run_dir.name,
        intended_authorization_id=f"auth-{run_dir.name}",
        host_output_directory_sha256=host_run_directory_sha256,
        authorization_state_directory_sha256=authorization_state_directory_sha256,
    )
    execution_binding_path = public_dir / "llf-canary-execution-binding.json"
    execution_binding_path.write_bytes(execution_binding_bytes(execution_binding))
    authorization = authorize_plan(
        plan,
        preregistration_path=preregistration_path,
        execution_binding=execution_binding,
        authorization_id=f"auth-{run_dir.name}",
        authorized_at_utc=NOW,
        run_directory=RUNTIME_OUTPUT_DIRECTORY,
        host_run_directory_sha256=host_run_directory_sha256,
        authorization_state_directory_sha256=(authorization_state_directory_sha256),
        run_id=run_dir.name,
        acknowledgement=LLF_CANARY_ACKNOWLEDGEMENT,
    )
    await run_live_plan(
        selected,
        plan=plan,
        authorization=authorization,
        execution_binding=execution_binding,
        preregistration_path=preregistration_path,
        contract=contract,
        caller=GoldOrFailureCaller(
            outputs,
            failed_case_id=failed_case_id,
            reasoning_effort=reasoning_effort,
        ),
        output_dir=run_dir,
        authorization_state_dir=authorization_state_dir,
        runtime_output_directory_sha256=run_directory_sha256(RUNTIME_OUTPUT_DIRECTORY),
        host_run_directory_sha256=host_run_directory_sha256,
        authorization_state_directory_sha256=(authorization_state_directory_sha256),
        run_id=run_dir.name,
        runtime_image_id=TEST_IMAGE_ID,
        clock=lambda: MEDIUM_FIXED_NOW if reasoning_effort == "medium" else FIXED_NOW,
    )
    return SealedCanary(
        run_dir=run_dir,
        authorization_state_dir=authorization_state_dir,
        preregistration_path=preregistration_path,
        execution_binding_path=execution_binding_path,
        execution_binding=execution_binding,
        host_run_directory_sha256=host_run_directory_sha256,
        authorization_state_directory_sha256=(authorization_state_directory_sha256),
    )


async def test_25_case_live_report_is_exact_sealed_and_deterministic(
    tmp_path: Path,
) -> None:
    chain = await _sealed_canary(tmp_path / "exact-canary")

    first = _score(chain)
    second = _score(chain)

    assert first == second
    assert first.purpose == "development_llf_canary_25"
    assert first.split == "development"
    assert first.operational.plan_case_count == 25
    assert first.operational.completed_count == 25
    assert first.operational.failed_count == 0
    assert first.operational.missing_reference_count == 0
    assert first.operational.usage.usage_known_count == 25
    assert first.operational.usage.usage_unknown_count == 0
    assert first.operational.usage.input_tokens == 250
    assert first.operational.usage.output_tokens == 75
    assert first.operational.latency.observed_case_count == 25
    assert first.operational.latency.complete_timing_count == 25
    assert first.operational.latency.p50_latency_ms is not None
    assert first.operational.latency.p95_latency_ms is not None
    assert first.operational.provider.response_id_count == 25
    assert first.operational.provider.unique_response_id_count == 25
    assert first.operational.provider.response_id_coverage == 1.0
    assert first.operational.provider.provider_model_counts == {PROVIDER_MODEL: 25}
    assert first.operational.provider.provider_response_object_counts == {PROVIDER_OBJECT: 25}
    assert first.operational.provider.provider_service_tier_counts == {PROVIDER_SERVICE_TIER: 25}
    assert first.inputs.preregistration_sha256 == _preregistration().preregistration_sha256
    assert first.inputs.execution_binding_sha256 == (
        chain.execution_binding.execution_binding_sha256
    )
    assert first.inputs.external_attempt_claim_count == 25
    assert (
        first.inputs.external_attempt_claim_inventory_sha256
        == json.loads((chain.run_dir / "summary.json").read_bytes())[
            "external_attempt_claim_inventory_sha256"
        ]
    )
    assert len({case.external_attempt_claim_sha256 for case in first.cases}) == 25
    assert len({case.external_attempt_claim_artifact_sha256 for case in first.cases}) == 25
    assert first.inputs.runtime_image_id == TEST_IMAGE_ID
    assert first.inputs.evaluator_transitively_bound_by_package_inventory is True
    assert first.metrics.semantic_case_count == 25
    assert first.metrics.exact_match_count == 25
    assert first.metrics.exact_match_accuracy == 1.0
    assert first.metrics.primary_structure.f1 == 1.0
    assert first.metrics.typed_components.f1 == 1.0
    assert first.exact_match_trial_interval.estimate == 1.0
    assert first.primary_structure_trial_interval.estimate == 1.0
    assert first.exact_match_trial_interval.cluster_count == 25
    assert len(first.cases) == 25
    assert all(case.semantic_status == "scored_completed" for case in first.cases)
    assert LlfLiveScoreReport.model_validate_json(report_bytes(first)) == first

    tampered = first.model_dump(mode="json")
    tampered["operational"]["usage"]["output_tokens"] += 1
    tampered["report_sha256"] = canonical_sha256(
        {key: value for key, value in tampered.items() if key != "report_sha256"}
    )
    with pytest.raises(ValueError, match="operational economics"):
        LlfLiveScoreReport.model_validate(tampered)


async def test_medium_profile_binds_authorizes_runs_and_scores(
    tmp_path: Path,
) -> None:
    chain = await _sealed_canary(
        tmp_path / "medium-canary",
        reasoning_effort="medium",
    )

    report = _score(chain)
    plan = json.loads((chain.run_dir / "plan.json").read_bytes())
    attempts = [json.loads(path.read_bytes()) for path in chain.run_dir.glob("attempt-*.json")]

    assert plan["luna"]["reasoning_effort"] == "medium"
    assert plan["luna"]["max_output_tokens"] == 32_768
    assert plan["luna"]["request_timeout_seconds"] == 240
    assert plan["reservation_per_case_usd"] == "0.043417600"
    assert plan["reserved_total_usd"] == "1.085440000"
    assert plan["budget_cap_usd"] == "1.250000000"
    assert chain.execution_binding.luna == frozen_luna_configuration("medium")
    assert chain.execution_binding.reservation_output_tokens == 32_768
    assert chain.execution_binding.reservation_per_case_usd == "0.043417600"
    assert chain.execution_binding.reserved_total_usd == "1.085440000"
    assert chain.execution_binding.budget_cap_usd == "1.250000000"
    assert len(attempts) == 25
    assert {attempt["reservation_usd"] for attempt in attempts} == {"0.043417600"}
    assert report.operational.completed_count == 25
    assert report.operational.failed_count == 0
    assert report.metrics.exact_match_count == 25


async def test_medium_recovery_uses_sealed_reservation_and_rejects_resigned_none_claim(
    tmp_path: Path,
) -> None:
    chain = await _sealed_canary(
        tmp_path / "medium-recovery",
        reasoning_effort="medium",
    )
    plan, _ = load_live_plan(chain.run_dir / "plan.json")
    authorization, _ = load_paid_authorization(chain.run_dir / "authorization.json")
    generation = load_llf_generation_split(LLF_DATA, "development")
    cases_by_id = {case.case_id: case for case in generation.cases}
    selected = tuple(cases_by_id[planned.case_id] for planned in plan.cases)
    contract = llf_semantic_output_contract()

    for path in (*chain.run_dir.glob("attempt-*.json"), *chain.run_dir.glob("case-*.json")):
        path.unlink()
    (chain.run_dir / "summary.json").unlink()

    recovery = recover_live_run(
        selected,
        plan=plan,
        authorization=authorization,
        execution_binding=chain.execution_binding,
        preregistration_path=chain.preregistration_path,
        contract=contract,
        output_dir=chain.run_dir,
        authorization_state_dir=chain.authorization_state_dir,
        runtime_output_directory_sha256=run_directory_sha256(RUNTIME_OUTPUT_DIRECTORY),
        host_run_directory_sha256=chain.host_run_directory_sha256,
        authorization_state_directory_sha256=(chain.authorization_state_directory_sha256),
        run_id=chain.run_dir.name,
        runtime_image_id=TEST_IMAGE_ID,
        now=MEDIUM_FIXED_NOW,
    )

    assert recovery.summary is not None
    assert recovery.summary.terminal_state == "completed"
    assert recovery.summary.completed_count == 0
    assert recovery.summary.failed_count == 25
    assert recovery.remaining_case_count == 0
    outcomes = tuple(
        CaseOutcome.model_validate_json(path.read_bytes())
        for path in sorted(chain.run_dir.glob("case-*.json"))
    )
    assert len(outcomes) == 25
    assert {outcome.failure.kind for outcome in outcomes if outcome.failure is not None} == {
        "interrupted_unknown"
    }
    assert {outcome.charged_cost_usd for outcome in outcomes} == {"0.043417600"}

    for path in (*chain.run_dir.glob("attempt-*.json"), *chain.run_dir.glob("case-*.json")):
        path.unlink()
    (chain.run_dir / "summary.json").unlink()
    external_path = chain.authorization_state_dir / (
        f"attempt-{authorization.authorization_sha256}-0001.json"
    )
    document = json.loads(external_path.read_bytes())
    pending = document["pending"]
    pending["reservation_usd"] = "0.006553600"
    pending.pop("pending_sha256")
    pending["pending_sha256"] = canonical_sha256(pending)
    document.pop("external_attempt_claim_sha256")
    document["external_attempt_claim_sha256"] = canonical_sha256(document)
    external_path.write_bytes(
        (json.dumps(document, sort_keys=True, separators=(",", ":")) + "\n").encode()
    )

    with pytest.raises(LiveRunError, match="external paid-attempt claim differs"):
        recover_live_run(
            selected,
            plan=plan,
            authorization=authorization,
            execution_binding=chain.execution_binding,
            preregistration_path=chain.preregistration_path,
            contract=contract,
            output_dir=chain.run_dir,
            authorization_state_dir=chain.authorization_state_dir,
            runtime_output_directory_sha256=run_directory_sha256(RUNTIME_OUTPUT_DIRECTORY),
            host_run_directory_sha256=chain.host_run_directory_sha256,
            authorization_state_directory_sha256=(chain.authorization_state_directory_sha256),
            run_id=chain.run_dir.name,
            runtime_image_id=TEST_IMAGE_ID,
            now=MEDIUM_FIXED_NOW,
        )
    assert not (chain.run_dir / "attempt-0001.json").exists()


async def test_failed_outcome_scores_as_empty_in_every_primary_count(
    tmp_path: Path,
) -> None:
    chain = await _sealed_canary(tmp_path / "failure-canary", fail_first=True)

    report = _score(chain)

    assert report.operational.completed_count == 24
    assert report.operational.failed_count == 1
    assert report.operational.failure_counts == {"timeout": 1}
    assert report.operational.usage.usage_known_count == 24
    assert report.operational.usage.usage_unknown_count == 1
    assert report.operational.provider.response_id_count == 24
    assert report.metrics.semantic_case_count == 25
    assert report.metrics.exact_match_count == 24
    failed = next(case for case in report.cases if case.outcome_status == "failed")
    assert failed.semantic_status == "scored_failure_as_empty"
    assert failed.exact_match is False
    assert failed.metrics is not None
    assert failed.metrics.primary_structure.true_positive == 0
    assert failed.metrics.primary_structure.false_positive == 0
    assert failed.metrics.primary_structure.false_negative > 0
    assert failed.metrics.primary_structure.f1 == 0.0


async def test_authorization_consumption_and_directory_inventory_fail_closed(
    tmp_path: Path,
) -> None:
    chain = await _sealed_canary(tmp_path / "authorization-canary")
    consumption_path = chain.run_dir / "authorization-consumed.json"
    original_consumption = consumption_path.read_bytes()
    changed = json.loads(original_consumption)
    changed["run_id"] = "conflicting-run-id"
    body = {key: value for key, value in changed.items() if key != "consumption_sha256"}
    changed["consumption_sha256"] = canonical_sha256(body)
    consumption_path.write_bytes(
        (json.dumps(changed, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
    )
    with pytest.raises(LlfLiveScoreError, match="consumption does not bind"):
        _score(chain)

    consumption_path.write_bytes(original_consumption)
    (chain.run_dir / "pending.json").write_bytes((chain.run_dir / "attempt-0001.json").read_bytes())
    with pytest.raises(LlfLiveScoreError, match="pending, or conflicting"):
        _score(chain)


async def test_tampered_summary_fails_before_any_reference_artifact_is_opened(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    chain = await _sealed_canary(tmp_path / "tamper-canary")
    summary_path = chain.run_dir / "summary.json"
    summary_path.write_bytes(
        summary_path.read_bytes().replace(b'"completed_count":25', b'"completed_count":24')
    )
    opened: list[str] = []
    original_read_bytes = Path.read_bytes

    def observed_read_bytes(path: Path) -> bytes:
        opened.append(path.name)
        if path.name.endswith("_references.jsonl") or "coverage" in path.name:
            raise AssertionError("tampered run must fail before references are mounted")
        return original_read_bytes(path)

    monkeypatch.setattr(Path, "read_bytes", observed_read_bytes)
    with pytest.raises(LlfLiveScoreError, match="strict model or internal hash"):
        _score(chain)
    assert "summary.json" in opened
    assert not any(name.endswith("_references.jsonl") for name in opened)


async def test_external_attempt_rollback_fails_before_references(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    chain = await _sealed_canary(tmp_path / "external-rollback-canary")
    authorization = json.loads((chain.run_dir / "authorization.json").read_bytes())
    rolled_back_claim = chain.authorization_state_dir / (
        f"attempt-{authorization['authorization_sha256']}-0025.json"
    )
    rolled_back_claim.unlink()
    opened: list[str] = []
    original_read_bytes = Path.read_bytes

    def reject_reference_read(path: Path) -> bytes:
        opened.append(path.name)
        if path.name.endswith("_references.jsonl") or "coverage" in path.name:
            raise AssertionError("external-ledger rollback must fail before references")
        return original_read_bytes(path)

    monkeypatch.setattr(Path, "read_bytes", reject_reference_read)
    with pytest.raises(LlfLiveScoreError, match="inventory is missing or has extras"):
        _score(chain)
    assert not any(name.endswith("_references.jsonl") for name in opened)


async def test_public_bytes_and_path_identities_fail_closed(
    tmp_path: Path,
) -> None:
    chain = await _sealed_canary(tmp_path / "public-path-canary")
    original = chain.preregistration_path.read_bytes()
    chain.preregistration_path.write_bytes(original.rstrip(b"\n") + b" \n")
    with pytest.raises(LlfLiveScoreError, match="does not reproduce"):
        _score(chain)
    chain.preregistration_path.write_bytes(original)

    with pytest.raises(LlfLiveScoreError, match="host run-directory identity"):
        score_live_llf_run(
            run_dir=chain.run_dir,
            authorization_state_dir=chain.authorization_state_dir,
            preregistration_path=chain.preregistration_path,
            execution_binding_path=chain.execution_binding_path,
            host_run_directory_sha256="0" * 64,
            authorization_state_directory_sha256=(chain.authorization_state_directory_sha256),
            dataset_dir=LLF_DATA,
            coverage_dir=COVERAGE_DIR,
        )


async def test_score_and_check_cli_round_trip(tmp_path: Path) -> None:
    chain = await _sealed_canary(tmp_path / "cli-canary")
    output = tmp_path / "score.json"
    common = [
        "--run-dir",
        str(chain.run_dir),
        "--authorization-state-dir",
        str(chain.authorization_state_dir),
        "--preregistration",
        str(chain.preregistration_path),
        "--execution-binding",
        str(chain.execution_binding_path),
        "--host-run-directory-sha256",
        chain.host_run_directory_sha256,
        "--authorization-state-directory-sha256",
        chain.authorization_state_directory_sha256,
        "--dataset-dir",
        str(LLF_DATA),
        "--coverage-dir",
        str(COVERAGE_DIR),
    ]

    assert main(["score", *common, "--output", str(output)]) == 0
    assert output.is_file()
    assert main(["check", *common, "--report", str(output)]) == 0


def test_offline_scorer_imports_no_transport_or_openai_module() -> None:
    script = (
        "import sys; import criteriabench.real_eval.llf_live_score; "
        "assert 'criteriabench.real_live.transport' not in sys.modules; "
        "assert 'openai' not in sys.modules"
    )
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr


def test_supported_score_wrapper_is_networkless_read_only_and_development_only() -> None:
    wrapper = (ROOT / "scripts" / "score-real-luna-canary.ps1").read_text(encoding="utf-8")
    assert "[string]$AuthorizationStateDirectory" in wrapper
    assert "[string]$PreregistrationPath" in wrapper
    assert "[string]$ExecutionBindingPath" in wrapper
    assert "--network=none" in wrapper
    assert "dst=/run/artifacts/output,readonly" in wrapper
    assert "dst=/run/authorization-state,readonly" in wrapper
    assert "dst=/run/public/llf-canary-preregistration.json,readonly" in wrapper
    assert "dst=/run/public/llf-canary-execution-binding.json,readonly" in wrapper
    assert "dst=/run/report" in wrapper
    assert "--authorization-state-dir', '/run/authorization-state'" in wrapper
    assert "--preregistration', '/run/public/llf-canary-preregistration.json'" in wrapper
    assert "--execution-binding', '/run/public/llf-canary-execution-binding.json'" in wrapper
    assert "development_references.jsonl" in wrapper
    assert "llf-semantic-coverage-development.json" in wrapper
    assert "test_references.jsonl" not in wrapper
    assert "llf-semantic-coverage-test.json" not in wrapper
    assert "records.jsonl" not in wrapper
