"""Offline-first command line for sealed Luna plans and explicitly paid execution."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Literal

from pydantic import BaseModel

from criteriabench.real_eval.llf_binding import load_llf_generation_split
from criteriabench.real_eval.models import GenerationCase
from criteriabench.real_live.contracts import (
    LLF_CANARY_ACKNOWLEDGEMENT,
    LOCKED_ACKNOWLEDGEMENT,
    CanaryExecutionBinding,
    LivePlan,
    PaidAuthorization,
    RunSummary,
    StrictOutputContract,
    llf_semantic_output_contract,
    verify_execution_implementation,
)
from criteriabench.real_live.planning import (
    authorize_plan,
    build_llf_canary_plan,
    build_locked_llf_plan,
    run_directory_sha256,
    select_development_canary,
    utc_now,
    verify_authorization,
    verify_authorized_run_directory,
    verify_execution_freshness,
    verify_execution_window_capacity,
    verify_plan_cases,
)
from criteriabench.real_live.runner import (
    FatalProviderConfigurationError,
    recover_live_run,
    run_live_plan,
)
from criteriabench.real_live.transport import (
    LunaResponsesCaller,
    assert_clean_openai_environment,
)

MAX_INPUT_ARTIFACT_BYTES = 20_000_000
MAX_STDIN_API_KEY_BYTES = 4_096


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    artifact_root = _artifact_root(args.artifact_root)
    if args.command in {"plan-llf-canary", "plan-locked"}:
        plan = _create_plan(args)
        _write_new_json(_artifact_file(args.output, artifact_root), plan)
        print(
            f"sealed_plan={plan.plan_sha256} cases={len(plan.cases)} cap_usd={plan.budget_cap_usd}"
        )
        return 0
    if args.command == "authorize":
        preregistration_path = _artifact_file(args.preregistration, artifact_root)
        plan, execution_binding = _load_verified_canary_chain(
            artifact_root=artifact_root,
            plan_path=args.plan,
            preregistration_path=args.preregistration,
            execution_binding_path=args.execution_binding,
        )
        acknowledgement = _authorization_acknowledgement(args, plan)
        authorization = authorize_plan(
            plan,
            preregistration_path=preregistration_path,
            execution_binding=execution_binding,
            authorization_id=args.authorization_id,
            authorized_at_utc=args.authorized_at_utc,
            run_directory=args.runtime_output_path,
            host_run_directory_sha256=args.host_run_directory_sha256,
            authorization_state_directory_sha256=(args.authorization_state_directory_sha256),
            run_id=args.run_id,
            acknowledgement=acknowledgement,
            expires_at_utc=args.expires_at_utc,
        )
        _write_new_json(_artifact_file(args.output, artifact_root), authorization)
        print(f"sealed_authorization={authorization.authorization_sha256}")
        return 0
    if args.command == "run":
        return asyncio.run(_run(args))
    if args.command == "recover":
        return _recover(args)
    raise AssertionError("unreachable command")


def _create_plan(args: argparse.Namespace) -> LivePlan:
    if args.command == "plan-locked":
        _verify_locked_plan_advancement(args)
        generation_root = _generation_root(args.generation_root)
        split = load_llf_generation_split(generation_root, "test")
        return build_locked_llf_plan(
            split.cases,
            dataset=split.dataset,
            contract=llf_semantic_output_contract(),
            created_at_utc=args.created_at_utc,
            runtime_image_id=args.runtime_image_id,
        )
    generation_root = _generation_root(args.generation_root)
    split = load_llf_generation_split(generation_root, "development")
    if args.command == "plan-llf-canary":
        plan, _ = build_llf_canary_plan(
            split.cases,
            dataset=split.dataset,
            contract=llf_semantic_output_contract(),
            created_at_utc=args.created_at_utc,
            runtime_image_id=args.runtime_image_id,
            reasoning_effort=args.reasoning_effort,
        )
        return plan
    raise ValueError("GraphV2 paid planning is disabled in Real v1")


def _verify_locked_plan_advancement(args: argparse.Namespace) -> None:
    """Require one exact, canonical PASS canary chain before locked planning."""

    # Lazy high-level import avoids a contracts/planning dependency cycle.
    from criteriabench.real_eval.llf_canary_preregistration import (
        load_advancement_decision,
        load_execution_binding,
        load_live_plan,
        load_live_score_report,
        load_paid_authorization,
        load_preregistration,
        verify_canary_advancement_decision,
    )

    artifact_root = _artifact_root(args.artifact_root)
    preregistration = load_preregistration(_artifact_file(args.preregistration, artifact_root))
    execution_binding = load_execution_binding(
        _artifact_file(args.execution_binding, artifact_root)
    )
    canary_plan, _ = load_live_plan(_artifact_file(args.canary_plan, artifact_root))
    authorization, _ = load_paid_authorization(
        _artifact_file(args.canary_authorization, artifact_root)
    )
    score_report, _ = load_live_score_report(_artifact_file(args.score_report, artifact_root))
    decision = load_advancement_decision(_artifact_file(args.advancement_decision, artifact_root))
    verify_canary_advancement_decision(
        preregistration,
        execution_binding,
        canary_plan,
        authorization,
        score_report,
        decision,
    )
    if (
        decision.advancement_status != "pass"
        or decision.proceed_to_separate_locked_authorization is not True
    ):
        raise ValueError("locked LLF planning requires an exact sealed PASS advancement decision")
    if canary_plan.luna.reasoning_effort != "none":
        raise ValueError(
            "medium-reasoning canary is development-only and cannot advance the locked-none lane"
        )
    if args.runtime_image_id != canary_plan.runtime_image_id:
        raise ValueError("locked LLF planning must use the exact canary runtime image ID")


async def _run(args: argparse.Namespace) -> int:
    # These checks intentionally precede environment access and client construction.
    if not args.live or not args.acknowledge_paid_api:
        raise ValueError("paid execution requires --live and --acknowledge-paid-api")
    artifact_root = _artifact_root(args.artifact_root)
    preregistration_path = _artifact_file(args.preregistration, artifact_root)
    plan, execution_binding = _load_verified_canary_chain(
        artifact_root=artifact_root,
        plan_path=args.plan,
        preregistration_path=args.preregistration,
        execution_binding_path=args.execution_binding,
    )
    authorization = _read_model(
        _artifact_file(args.authorization, artifact_root), PaidAuthorization
    )
    contract = _contract_for_plan(plan)
    cases = _cases_for_plan(plan, _generation_root(args.generation_root))
    verify_authorization(
        plan,
        authorization,
        execution_binding,
        preregistration_path=preregistration_path,
    )
    verify_plan_cases(plan, cases)
    output_dir = _artifact_directory(args.output_dir, artifact_root)
    runtime_output_directory_sha256 = run_directory_sha256(output_dir)
    verify_authorized_run_directory(
        authorization,
        runtime_output_directory_sha256,
        args.host_run_directory_sha256,
        args.authorization_state_directory_sha256,
        args.run_id,
    )
    authorization_state_dir = _authorization_state_directory(args.authorization_state_dir)
    if args.runtime_image_id != plan.runtime_image_id:
        raise ValueError("runtime image ID differs from the exact sealed plan")
    verify_execution_implementation(plan.execution_implementation)
    execution_time = utc_now()
    recovery = recover_live_run(
        cases,
        plan=plan,
        authorization=authorization,
        execution_binding=execution_binding,
        preregistration_path=preregistration_path,
        contract=contract,
        output_dir=output_dir,
        authorization_state_dir=authorization_state_dir,
        runtime_output_directory_sha256=runtime_output_directory_sha256,
        host_run_directory_sha256=args.host_run_directory_sha256,
        authorization_state_directory_sha256=(args.authorization_state_directory_sha256),
        run_id=args.run_id,
        runtime_image_id=args.runtime_image_id,
        now=execution_time,
    )
    if recovery.summary is not None:
        if recovery.summary.terminal_state == "aborted":
            raise FatalProviderConfigurationError(recovery.summary)
        _print_summary(recovery.summary)
        return 0
    verify_execution_freshness(plan, authorization, now=execution_time)
    verify_execution_window_capacity(
        plan,
        authorization,
        now=execution_time,
        remaining_case_count=recovery.remaining_case_count,
    )

    assert_clean_openai_environment(os.environ)
    api_key: str | None
    if args.api_key_stdin:
        if _process_openai_api_key():
            raise ValueError("OPENAI_API_KEY must be unset when --api-key-stdin is used")
        api_key = _read_api_key_stdin()
    else:
        api_key = _process_openai_api_key()
    if not api_key:
        raise ValueError("OPENAI_API_KEY is not configured in the current process")
    caller = LunaResponsesCaller.from_api_key(api_key, plan.luna)
    api_key = None
    try:
        summary = await run_live_plan(
            cases,
            plan=plan,
            authorization=authorization,
            execution_binding=execution_binding,
            preregistration_path=preregistration_path,
            contract=contract,
            caller=caller,
            output_dir=output_dir,
            authorization_state_dir=authorization_state_dir,
            runtime_output_directory_sha256=runtime_output_directory_sha256,
            host_run_directory_sha256=args.host_run_directory_sha256,
            authorization_state_directory_sha256=(args.authorization_state_directory_sha256),
            run_id=args.run_id,
            runtime_image_id=args.runtime_image_id,
        )
    finally:
        await caller.aclose()
    _print_summary(summary)
    return 0


def _recover(args: argparse.Namespace) -> int:
    """Reconcile and report sealed state without reading a key or constructing a client."""

    artifact_root = _artifact_root(args.artifact_root)
    preregistration_path = _artifact_file(args.preregistration, artifact_root)
    plan, execution_binding = _load_verified_canary_chain(
        artifact_root=artifact_root,
        plan_path=args.plan,
        preregistration_path=args.preregistration,
        execution_binding_path=args.execution_binding,
    )
    authorization = _read_model(
        _artifact_file(args.authorization, artifact_root), PaidAuthorization
    )
    contract = _contract_for_plan(plan)
    cases = _cases_for_plan(plan, _generation_root(args.generation_root))
    verify_authorization(
        plan,
        authorization,
        execution_binding,
        preregistration_path=preregistration_path,
    )
    verify_plan_cases(plan, cases)
    output_dir = _artifact_directory(args.output_dir, artifact_root)
    runtime_output_directory_sha256 = run_directory_sha256(output_dir)
    verify_authorized_run_directory(
        authorization,
        runtime_output_directory_sha256,
        args.host_run_directory_sha256,
        args.authorization_state_directory_sha256,
        args.run_id,
    )
    authorization_state_dir = _authorization_state_directory(args.authorization_state_dir)
    if args.runtime_image_id != plan.runtime_image_id:
        raise ValueError("runtime image ID differs from the exact sealed plan")
    verify_execution_implementation(plan.execution_implementation)
    recovery = recover_live_run(
        cases,
        plan=plan,
        authorization=authorization,
        execution_binding=execution_binding,
        preregistration_path=preregistration_path,
        contract=contract,
        output_dir=output_dir,
        authorization_state_dir=authorization_state_dir,
        runtime_output_directory_sha256=runtime_output_directory_sha256,
        host_run_directory_sha256=args.host_run_directory_sha256,
        authorization_state_directory_sha256=(args.authorization_state_directory_sha256),
        run_id=args.run_id,
        runtime_image_id=args.runtime_image_id,
        now=utc_now(),
    )
    if recovery.summary is not None:
        _print_summary(recovery.summary)
    print(f"recovery_remaining={recovery.remaining_case_count}")
    return 0


def _print_summary(summary: RunSummary) -> None:
    print(
        f"summary={summary.summary_sha256} completed={summary.completed_count} "
        f"failed={summary.failed_count} charged_usd={summary.charged_total_usd}"
    )


def _process_openai_api_key() -> str | None:
    """Read only the process-scoped key; never load dotenv files."""

    return os.environ.get("OPENAI_API_KEY")


def _read_api_key_stdin() -> str:
    """Read exactly one bounded line without echoing or persisting it."""

    raw = sys.stdin.buffer.readline(MAX_STDIN_API_KEY_BYTES + 2)
    if len(raw) > MAX_STDIN_API_KEY_BYTES + 1:
        raise ValueError("stdin API key exceeds the bounded input length")
    key_bytes = raw.rstrip(b"\r\n")
    if not key_bytes or b"\n" in key_bytes or b"\r" in key_bytes:
        raise ValueError("stdin API key must be exactly one non-empty line")
    if sys.stdin.buffer.read(1) != b"":
        raise ValueError("stdin API key input contains trailing data")
    try:
        return key_bytes.decode("utf-8")
    except UnicodeDecodeError as error:
        raise ValueError("stdin API key must be valid UTF-8") from error


def _cases_for_plan(
    plan: LivePlan,
    generation_root: Path,
) -> tuple[GenerationCase, ...]:
    split_name: Literal["test", "development"] = (
        "test" if plan.purpose == "locked_llf_test" else "development"
    )
    split = load_llf_generation_split(generation_root, split_name)
    if split_name == "test":
        return split.cases
    return select_development_canary(split.cases)


def _contract_for_plan(plan: LivePlan) -> StrictOutputContract[BaseModel]:
    if plan.output_contract.track == "llf_semantic_ast":
        return llf_semantic_output_contract()
    raise ValueError("GraphV2 paid execution is disabled in Real v1")


def _authorization_acknowledgement(args: argparse.Namespace, plan: LivePlan) -> str:
    flags = {
        "development_llf_canary_25": (
            args.acknowledge_llf_canary,
            LLF_CANARY_ACKNOWLEDGEMENT,
        ),
        "locked_llf_test": (
            args.acknowledge_locked_llf,
            LOCKED_ACKNOWLEDGEMENT,
        ),
    }
    acknowledged, phrase = flags[plan.purpose]
    if not acknowledged:
        raise ValueError("the purpose-specific paid-plan acknowledgement is required")
    if (
        sum(
            (
                args.acknowledge_llf_canary,
                args.acknowledge_locked_llf,
            )
        )
        != 1
    ):
        raise ValueError("select exactly one purpose-specific acknowledgement")
    return phrase


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    for name in ("plan-llf-canary", "plan-locked"):
        command = subparsers.add_parser(name)
        command.add_argument("--artifact-root", type=Path, required=True)
        command.add_argument("--created-at-utc", required=True)
        command.add_argument("--runtime-image-id", required=True)
        command.add_argument("--generation-root", type=Path, required=True)
        command.add_argument("--output", type=Path, required=True)
        if name == "plan-llf-canary":
            command.add_argument(
                "--reasoning-effort",
                choices=("none", "medium"),
                default="none",
            )
        if name == "plan-locked":
            command.add_argument("--preregistration", type=Path, required=True)
            command.add_argument("--execution-binding", type=Path, required=True)
            command.add_argument("--canary-plan", type=Path, required=True)
            command.add_argument("--canary-authorization", type=Path, required=True)
            command.add_argument("--score-report", type=Path, required=True)
            command.add_argument("--advancement-decision", type=Path, required=True)

    authorize = subparsers.add_parser("authorize")
    authorize.add_argument("--artifact-root", type=Path, required=True)
    authorize.add_argument("--plan", type=Path, required=True)
    authorize.add_argument("--preregistration", type=Path, required=True)
    authorize.add_argument("--execution-binding", type=Path, required=True)
    authorize.add_argument("--output", type=Path, required=True)
    authorize.add_argument("--authorization-id", required=True)
    authorize.add_argument("--authorized-at-utc", required=True)
    authorize.add_argument("--expires-at-utc")
    authorize.add_argument("--run-id", required=True)
    authorize.add_argument("--runtime-output-path", type=Path, required=True)
    authorize.add_argument("--host-run-directory-sha256", required=True)
    authorize.add_argument("--authorization-state-directory-sha256", required=True)
    authorize.add_argument("--acknowledge-llf-canary", action="store_true")
    authorize.add_argument("--acknowledge-locked-llf", action="store_true")

    recover = subparsers.add_parser("recover")
    _add_run_artifact_arguments(recover)

    run = subparsers.add_parser("run")
    _add_run_artifact_arguments(run)
    run.add_argument("--live", action="store_true")
    run.add_argument("--acknowledge-paid-api", action="store_true")
    run.add_argument("--api-key-stdin", action="store_true")
    return parser


def _add_run_artifact_arguments(command: argparse.ArgumentParser) -> None:
    command.add_argument("--artifact-root", type=Path, required=True)
    command.add_argument("--plan", type=Path, required=True)
    command.add_argument("--authorization", type=Path, required=True)
    command.add_argument("--preregistration", type=Path, required=True)
    command.add_argument("--execution-binding", type=Path, required=True)
    command.add_argument("--generation-root", type=Path, required=True)
    command.add_argument("--output-dir", type=Path, required=True)
    command.add_argument("--authorization-state-dir", type=Path, required=True)
    command.add_argument("--host-run-directory-sha256", required=True)
    command.add_argument("--authorization-state-directory-sha256", required=True)
    command.add_argument("--run-id", required=True)
    command.add_argument("--runtime-image-id", required=True)


def _artifact_file(path: Path, artifact_root: Path) -> Path:
    resolved = _contained_artifact_path(path, artifact_root)
    if resolved.suffix.lower() != ".json":
        raise ValueError("sealed plan and authorization artifacts must be JSON files")
    return resolved


def _generation_root(path: Path) -> Path:
    resolved = path.resolve()
    if not resolved.is_dir():
        raise ValueError("generation-only artifact root must be an existing directory")
    return resolved


def _artifact_directory(path: Path, artifact_root: Path) -> Path:
    resolved = _contained_artifact_path(path, artifact_root)
    if resolved.exists() and not resolved.is_dir():
        raise ValueError("live-run output path must be a directory")
    return resolved


def _authorization_state_directory(path: Path) -> Path:
    if path.is_symlink():
        raise ValueError("authorization state directory cannot be a symbolic link")
    resolved = path.resolve(strict=True)
    if not resolved.is_dir():
        raise ValueError("authorization state path must be an existing directory")
    return resolved


def _load_verified_canary_chain(
    *,
    artifact_root: Path,
    plan_path: Path,
    preregistration_path: Path,
    execution_binding_path: Path,
) -> tuple[LivePlan, CanaryExecutionBinding]:
    # Lazy high-level import avoids a contracts/planning dependency cycle.
    from criteriabench.real_eval.llf_canary_preregistration import (
        load_execution_binding,
        load_preregistration,
        verify_canary_execution_binding,
    )

    sealed_plan_path = _artifact_file(plan_path, artifact_root)
    plan = _read_model(sealed_plan_path, LivePlan)
    preregistration = load_preregistration(_artifact_file(preregistration_path, artifact_root))
    binding = load_execution_binding(_artifact_file(execution_binding_path, artifact_root))
    verify_canary_execution_binding(
        preregistration,
        binding,
        plan,
        plan_artifact_sha256=hashlib.sha256(sealed_plan_path.read_bytes()).hexdigest(),
    )
    return plan, binding


def _artifact_root(path: Path) -> Path:
    resolved = path.resolve()
    if not resolved.is_dir():
        raise ValueError("explicit real-live artifact root must be an existing directory")
    return resolved


def _contained_artifact_path(path: Path, artifact_root: Path) -> Path:
    candidate = path if path.is_absolute() else artifact_root / path
    resolved = candidate.resolve()
    root = artifact_root.resolve()
    if resolved == root or root not in resolved.parents:
        raise ValueError("real live artifacts must remain under the explicit artifact root")
    return resolved


def _write_new_json(path: Path, model: BaseModel) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = (
        json.dumps(
            model.model_dump(mode="json"),
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode("utf-8")
    with path.open("xb") as file:
        file.write(payload)
        file.flush()
        os.fsync(file.fileno())


def _read_model[TModel: BaseModel](path: Path, model_type: type[TModel]) -> TModel:
    raw = path.read_bytes()
    if len(raw) > MAX_INPUT_ARTIFACT_BYTES:
        raise ValueError("sealed input artifact exceeds the size limit")
    return model_type.model_validate_json(raw)


if __name__ == "__main__":
    raise SystemExit(main())
