"""Deterministic selection, sealing, and authorization for paid Luna plans."""

from __future__ import annotations

import hashlib
import json
import os
from collections import defaultdict
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from decimal import ROUND_FLOOR, Decimal
from pathlib import Path, PurePosixPath, PureWindowsPath

from pydantic import BaseModel

from criteriabench.real_eval.integrity import (
    canonical_sha256,
    case_set_sha256,
    validate_generation_cases,
)
from criteriabench.real_eval.models import GenerationCase, GenerationDatasetBinding
from criteriabench.real_live.contracts import (
    CANARY_CASE_COUNT,
    GRAPH_PRODUCT_CANARY_ACKNOWLEDGEMENT,
    LLF_CANARY_ACKNOWLEDGEMENT,
    LLF_PROMPT_EXAMPLE_TRIAL_IDS,
    LOCKED_ACKNOWLEDGEMENT,
    LOCKED_BUDGET_CAP_USD,
    LOCKED_CASE_COUNT,
    MAX_INPUT_TOKENS_RESERVED,
    CanaryExecutionBinding,
    LivePlan,
    LivePlanPayload,
    PaidAuthorization,
    PaidAuthorizationPayload,
    PlannedCase,
    PlanPurpose,
    ReasoningEffort,
    StrictOutputContract,
    canary_budget_cap_usd,
    freeze_output_contract,
    frozen_execution_implementation,
    frozen_luna_configuration,
    frozen_pricing,
    money,
    parse_utc_timestamp,
    reservation_per_case_usd,
)

CANARY_SELECTION_ALGORITHM = "polarity-length-tertile-trial-stratified-sha256-v1"
LOCKED_SELECTION_ALGORITHM = "frozen-case-order-v1"
CANARY_SELECTION_SEED = "criteriabench-real-v1-luna-canary"


def select_development_canary(
    cases: Sequence[GenerationCase],
) -> tuple[GenerationCase, ...]:
    """Select 25 unique-trial cases stratified by polarity and source length."""

    validate_generation_cases(cases)
    eligible = tuple(case for case in cases if case.trial_id not in LLF_PROMPT_EXAMPLE_TRIAL_IDS)
    if len({case.trial_id for case in eligible}) < CANARY_CASE_COUNT:
        raise ValueError("development split must contain at least 25 distinct trials")
    lengths = sorted(len(case.source_text) for case in eligible)
    lower_cut = lengths[len(lengths) // 3]
    upper_cut = lengths[(2 * len(lengths)) // 3]
    buckets: dict[str, list[GenerationCase]] = defaultdict(list)
    for case in eligible:
        length_bin = (
            "short"
            if len(case.source_text) <= lower_cut
            else "medium"
            if len(case.source_text) <= upper_cut
            else "long"
        )
        buckets[f"{case.criterion_kind.value}:{length_bin}"].append(case)
    quotas = _stratified_quotas(
        {name: len(values) for name, values in buckets.items()},
        total=CANARY_CASE_COUNT,
    )
    selected: list[GenerationCase] = []
    used_trials: set[str] = set()
    shortfalls = 0
    for bucket_name in sorted(buckets):
        ranked = sorted(
            buckets[bucket_name],
            key=lambda case: (_rank(bucket_name, case.case_id), case.case_id),
        )
        picked = 0
        for case in ranked:
            if case.trial_id in used_trials:
                continue
            selected.append(case)
            used_trials.add(case.trial_id)
            picked += 1
            if picked == quotas[bucket_name]:
                break
        shortfalls += quotas[bucket_name] - picked
    if shortfalls:
        remaining = sorted(
            (case for case in eligible if case.trial_id not in used_trials),
            key=lambda case: (_rank("stratified-shortfall", case.case_id), case.case_id),
        )
        for case in remaining[:shortfalls]:
            selected.append(case)
            used_trials.add(case.trial_id)
    if len(selected) != CANARY_CASE_COUNT:
        raise ValueError("could not select 25 unique-trial stratified canary cases")
    return tuple(selected)


def build_llf_canary_plan(
    cases: Sequence[GenerationCase],
    *,
    dataset: GenerationDatasetBinding,
    contract: StrictOutputContract[BaseModel],
    created_at_utc: str,
    runtime_image_id: str,
    reasoning_effort: ReasoningEffort = "none",
    expires_at_utc: str | None = None,
) -> tuple[LivePlan, tuple[GenerationCase, ...]]:
    """Build the apples-to-apples LLF semantic-AST development canary."""

    _validate_source_split(cases, dataset, expected_split="development")
    if contract.track != "llf_semantic_ast":
        raise ValueError("LLF quality canary requires a lossless LLF semantic output contract")
    selected = select_development_canary(cases)
    plan = _build_plan(
        selected,
        dataset=dataset,
        contract=contract,
        created_at_utc=created_at_utc,
        runtime_image_id=runtime_image_id,
        expires_at_utc=expires_at_utc,
        purpose="development_llf_canary_25",
        selection_algorithm=CANARY_SELECTION_ALGORITHM,
        reasoning_effort=reasoning_effort,
        budget_cap_usd=money(canary_budget_cap_usd(frozen_luna_configuration(reasoning_effort))),
        requires_separate_locked_authorization=False,
    )
    return plan, selected


def build_graph_product_canary_plan(
    cases: Sequence[GenerationCase],
    *,
    dataset: GenerationDatasetBinding,
    contract: StrictOutputContract[BaseModel],
    created_at_utc: str,
    runtime_image_id: str,
    expires_at_utc: str | None = None,
) -> tuple[LivePlan, tuple[GenerationCase, ...]]:
    """Refuse the unvalidated GraphV2 paid lane in Real v1."""

    del cases, dataset, contract, created_at_utc, runtime_image_id, expires_at_utc
    raise ValueError(
        "GraphV2 paid canary is disabled until case-aware evidence validation is bound"
    )


def build_locked_llf_plan(
    cases: Sequence[GenerationCase],
    *,
    dataset: GenerationDatasetBinding,
    contract: StrictOutputContract[BaseModel],
    created_at_utc: str,
    runtime_image_id: str,
    expires_at_utc: str | None = None,
) -> LivePlan:
    """Build the locked LLF plan; GraphV2 is structurally rejected here."""

    _validate_source_split(cases, dataset, expected_split="test")
    if len(cases) != LOCKED_CASE_COUNT:
        raise ValueError("frozen locked LLF split must contain exactly 1800 cases")
    if contract.track != "llf_semantic_ast":
        raise ValueError(
            "locked LLF quality benchmark requires a lossless LLF semantic output contract"
        )
    return _build_plan(
        tuple(cases),
        dataset=dataset,
        contract=contract,
        created_at_utc=created_at_utc,
        runtime_image_id=runtime_image_id,
        expires_at_utc=expires_at_utc,
        purpose="locked_llf_test",
        selection_algorithm=LOCKED_SELECTION_ALGORITHM,
        reasoning_effort="none",
        budget_cap_usd=money(LOCKED_BUDGET_CAP_USD),
        requires_separate_locked_authorization=True,
    )


def authorize_plan(
    plan: LivePlan,
    *,
    preregistration_path: Path,
    execution_binding: CanaryExecutionBinding,
    authorization_id: str,
    authorized_at_utc: str,
    run_directory: Path | str,
    host_run_directory_sha256: str,
    authorization_state_directory_sha256: str,
    run_id: str,
    acknowledgement: str,
    expires_at_utc: str | None = None,
) -> PaidAuthorization:
    """Seal an operator acknowledgement bound to one exact plan and cap."""

    if plan.purpose == "locked_llf_test":
        raise ValueError(
            "locked LLF paid execution authorization is structurally disabled until "
            "the advancement decision and bounded execution-window protocol are implemented"
        )
    verify_preregistered_canary_chain(
        plan,
        execution_binding,
        preregistration_path=preregistration_path,
    )
    verify_canary_execution_binding(
        plan,
        execution_binding,
        run_id=run_id,
        authorization_id=authorization_id,
        runtime_output_directory_sha256=run_directory_sha256(run_directory),
        host_output_directory_sha256=host_run_directory_sha256,
        authorization_state_directory_sha256=authorization_state_directory_sha256,
    )
    expected = _expected_acknowledgement(plan)
    if acknowledgement != expected:
        raise ValueError("paid authorization acknowledgement is not exact")
    authorized_at = parse_utc_timestamp(authorized_at_utc)
    plan_created = parse_utc_timestamp(plan.created_at_utc)
    plan_expires = parse_utc_timestamp(plan.expires_at_utc)
    authorization_expires = (
        parse_utc_timestamp(expires_at_utc)
        if expires_at_utc is not None
        else min(authorized_at + timedelta(hours=2), plan_expires)
    )
    if not (plan_created <= authorized_at < authorization_expires <= plan_expires):
        raise ValueError("authorization lifetime must fall inside the sealed plan lifetime")
    payload = PaidAuthorizationPayload(
        schema_version="real-live-authorization-v1",
        authorization_id=authorization_id,
        authorized_at_utc=authorized_at_utc,
        expires_at_utc=_format_utc(authorization_expires),
        plan_sha256=plan.plan_sha256,
        preregistration_sha256=execution_binding.preregistration_sha256,
        preregistration_artifact_sha256=(execution_binding.preregistration_artifact_sha256),
        execution_binding_sha256=execution_binding.execution_binding_sha256,
        execution_binding_artifact_sha256=_execution_binding_artifact_sha256(execution_binding),
        purpose=plan.purpose,
        authorized_case_count=len(plan.cases),
        authorized_budget_cap_usd=plan.budget_cap_usd,
        run_directory_sha256=run_directory_sha256(run_directory),
        host_run_directory_sha256=host_run_directory_sha256,
        authorization_state_directory_sha256=authorization_state_directory_sha256,
        run_id=run_id,
        acknowledgement=acknowledgement,
    )
    body = payload.model_dump(mode="json")
    authorization = PaidAuthorization.model_validate(
        {**body, "authorization_sha256": canonical_sha256(body)}
    )
    verify_execution_window_capacity(
        plan,
        authorization,
        now=authorized_at,
        remaining_case_count=len(plan.cases),
    )
    return authorization


def verify_authorization(
    plan: LivePlan,
    authorization: PaidAuthorization,
    execution_binding: CanaryExecutionBinding,
    *,
    preregistration_path: Path,
) -> None:
    """Reject stale, reused, mismatched, or weaker authorization artifacts."""

    verify_preregistered_canary_chain(
        plan,
        execution_binding,
        preregistration_path=preregistration_path,
    )
    verify_canary_execution_binding(
        plan,
        execution_binding,
        run_id=authorization.run_id,
        authorization_id=authorization.authorization_id,
        runtime_output_directory_sha256=authorization.run_directory_sha256,
        host_output_directory_sha256=authorization.host_run_directory_sha256,
        authorization_state_directory_sha256=(authorization.authorization_state_directory_sha256),
    )
    expected_acknowledgement = _expected_acknowledgement(plan)
    if authorization.plan_sha256 != plan.plan_sha256:
        raise ValueError("authorization does not bind the exact sealed plan")
    if (
        authorization.preregistration_sha256 != execution_binding.preregistration_sha256
        or authorization.preregistration_artifact_sha256
        != execution_binding.preregistration_artifact_sha256
        or authorization.execution_binding_sha256 != execution_binding.execution_binding_sha256
        or authorization.execution_binding_artifact_sha256
        != _execution_binding_artifact_sha256(execution_binding)
    ):
        raise ValueError("authorization does not bind the exact public execution binding")
    if authorization.purpose != plan.purpose:
        raise ValueError("authorization purpose does not match the plan")
    if authorization.authorized_case_count != len(plan.cases):
        raise ValueError("authorization case count does not match the plan")
    if authorization.authorized_budget_cap_usd != plan.budget_cap_usd:
        raise ValueError("authorization cap does not match the plan")
    if authorization.acknowledgement != expected_acknowledgement:
        raise ValueError("authorization acknowledgement is invalid for this plan")
    if plan.purpose == "locked_llf_test" and not plan.requires_separate_locked_authorization:
        raise ValueError("locked run is missing its separate-authorization requirement")
    if not (
        parse_utc_timestamp(plan.created_at_utc)
        <= parse_utc_timestamp(authorization.authorized_at_utc)
        < parse_utc_timestamp(authorization.expires_at_utc)
        <= parse_utc_timestamp(plan.expires_at_utc)
    ):
        raise ValueError("authorization lifetime is outside the sealed plan lifetime")


def verify_preregistered_canary_chain(
    plan: LivePlan,
    execution_binding: CanaryExecutionBinding,
    *,
    preregistration_path: Path,
) -> None:
    """Load exact canonical preregistration bytes and verify the full public chain."""

    # Lazy import avoids the intentional preregistration -> planning dependency
    # during offline construction while keeping the paid core fail-closed.
    from criteriabench.real_eval.llf_canary_preregistration import load_preregistration
    from criteriabench.real_eval.llf_canary_preregistration import (
        verify_canary_execution_binding as verify_public_canary_execution_binding,
    )

    preregistration = load_preregistration(preregistration_path)
    verify_public_canary_execution_binding(
        preregistration,
        execution_binding,
        plan,
        plan_artifact_sha256=hashlib.sha256(_compact_model_bytes(plan)).hexdigest(),
    )


def verify_execution_freshness(
    plan: LivePlan,
    authorization: PaidAuthorization,
    *,
    now: datetime,
) -> None:
    """Enforce plan, authorization, and price review immediately before paid use."""

    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("freshness clock must be timezone-aware")
    current = now.astimezone(UTC)
    windows = (
        (
            parse_utc_timestamp(plan.created_at_utc),
            parse_utc_timestamp(plan.expires_at_utc),
            "plan",
        ),
        (
            parse_utc_timestamp(authorization.authorized_at_utc),
            parse_utc_timestamp(authorization.expires_at_utc),
            "authorization",
        ),
        (
            parse_utc_timestamp(plan.pricing.reviewed_at_utc),
            parse_utc_timestamp(plan.pricing.valid_through_utc),
            "pricing review",
        ),
    )
    for starts_at, expires_at, label in windows:
        if not (starts_at <= current <= expires_at):
            raise ValueError(f"{label} is not fresh at execution time")


def verify_execution_window_capacity(
    plan: LivePlan,
    authorization: PaidAuthorization,
    *,
    now: datetime,
    remaining_case_count: int,
) -> None:
    """Require the full conservative sequential runtime to fit every live window."""

    if remaining_case_count < 0 or remaining_case_count > len(plan.cases):
        raise ValueError("remaining case count is outside the sealed plan")
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("execution-window clock must be timezone-aware")
    current = now.astimezone(UTC)
    required_until = current + timedelta(
        seconds=remaining_case_count * plan.luna.request_timeout_seconds
    )
    window_end = min(
        parse_utc_timestamp(plan.expires_at_utc),
        parse_utc_timestamp(authorization.expires_at_utc),
        parse_utc_timestamp(plan.pricing.valid_through_utc),
    )
    if required_until > window_end:
        raise ValueError(
            "conservative sequential runtime does not fit the sealed "
            "plan, authorization, and pricing windows"
        )


def verify_authorized_run_directory(
    authorization: PaidAuthorization,
    runtime_output_directory_sha256: str,
    host_run_directory_sha256: str,
    authorization_state_directory_sha256: str,
    run_id: str,
) -> None:
    if authorization.run_directory_sha256 != runtime_output_directory_sha256:
        raise ValueError("authorization is bound to a different live-run directory")
    if authorization.run_id != run_id:
        raise ValueError("authorization is bound to a different logical live-run ID")
    if authorization.host_run_directory_sha256 != host_run_directory_sha256:
        raise ValueError("authorization is bound to a different host live-run directory")
    if authorization.authorization_state_directory_sha256 != authorization_state_directory_sha256:
        raise ValueError("authorization is bound to a different durable state directory")


def run_directory_sha256(run_directory: Path | str) -> str:
    if isinstance(run_directory, Path):
        normalized = os.path.normcase(str(run_directory.resolve()))
    elif run_directory.startswith("/"):
        posix_path = PurePosixPath(run_directory)
        if ".." in posix_path.parts:
            raise ValueError("runtime output path must be absolute and normalized")
        normalized = posix_path.as_posix()
    else:
        windows_path = PureWindowsPath(run_directory)
        if not windows_path.is_absolute() or ".." in windows_path.parts:
            raise ValueError("runtime output path must be absolute and normalized")
        normalized = os.path.normcase(str(windows_path))
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def verify_canary_execution_binding(
    plan: LivePlan,
    binding: CanaryExecutionBinding,
    *,
    run_id: str,
    authorization_id: str,
    runtime_output_directory_sha256: str,
    host_output_directory_sha256: str,
    authorization_state_directory_sha256: str,
) -> None:
    """Reproduce all runner-relevant fields of the one-shot public binding."""

    plan_bytes = _compact_model_bytes(plan)
    comparisons: tuple[tuple[str, object, object], ...] = (
        ("plan_sha256", binding.plan_sha256, plan.plan_sha256),
        (
            "plan_artifact_sha256",
            binding.plan_artifact_sha256,
            hashlib.sha256(plan_bytes).hexdigest(),
        ),
        ("runtime_image_id", binding.runtime_image_id, plan.runtime_image_id),
        (
            "runtime_output_directory_sha256",
            binding.runtime_output_directory_sha256,
            runtime_output_directory_sha256,
        ),
        (
            "host_output_directory_sha256",
            binding.host_output_directory_sha256,
            host_output_directory_sha256,
        ),
        (
            "authorization_state_directory_sha256",
            binding.authorization_state_directory_sha256,
            authorization_state_directory_sha256,
        ),
        ("intended_run_id", binding.intended_run_id, run_id),
        ("intended_authorization_id", binding.intended_authorization_id, authorization_id),
        ("purpose", binding.purpose, plan.purpose),
        ("case_count", binding.case_count, len(plan.cases)),
        (
            "selected_case_set_sha256",
            binding.selected_case_set_sha256,
            plan.selected_case_set_sha256,
        ),
        ("source_dataset", binding.source_dataset, plan.source_dataset),
        ("selection_algorithm", binding.selection_algorithm, plan.selection_algorithm),
        ("output_contract", binding.output_contract, plan.output_contract),
        ("luna", binding.luna, plan.luna),
        ("execution", binding.execution, plan.execution_implementation),
        ("pricing", binding.pricing, plan.pricing),
        (
            "reservation_input_tokens",
            binding.reservation_input_tokens,
            plan.reservation_input_tokens,
        ),
        (
            "reservation_output_tokens",
            binding.reservation_output_tokens,
            plan.reservation_output_tokens,
        ),
        (
            "reservation_per_case_usd",
            binding.reservation_per_case_usd,
            plan.reservation_per_case_usd,
        ),
        ("reserved_total_usd", binding.reserved_total_usd, plan.reserved_total_usd),
        ("budget_cap_usd", binding.budget_cap_usd, plan.budget_cap_usd),
        (
            "requires_separate_locked_authorization",
            binding.requires_separate_locked_authorization,
            plan.requires_separate_locked_authorization,
        ),
    )
    for label, observed, expected in comparisons:
        if observed != expected:
            raise ValueError(f"execution binding {label} differs from the sealed plan/run")


def _execution_binding_artifact_sha256(binding: CanaryExecutionBinding) -> str:
    payload = (
        json.dumps(
            binding.model_dump(mode="json"),
            ensure_ascii=False,
            allow_nan=False,
            indent=2,
            sort_keys=True,
        )
        + "\n"
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


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
    ).encode("utf-8")


def verify_plan_cases(plan: LivePlan, cases: Sequence[GenerationCase]) -> None:
    """Bind runtime source text to the exact local identities in a sealed plan."""

    validate_generation_cases(cases)
    if len(cases) != len(plan.cases):
        raise ValueError("runtime case count does not match the sealed plan")
    if case_set_sha256(cases) != plan.selected_case_set_sha256:
        raise ValueError("runtime case-set hash does not match the sealed plan")
    for case, planned in zip(cases, plan.cases, strict=True):
        expected = {
            "case_id": case.case_id,
            "trial_id": case.trial_id,
            "document_id": case.document_id,
            "criterion_kind": case.criterion_kind.value,
            "source_sha256": case.source_sha256,
        }
        actual = planned.model_dump(mode="json", exclude={"ordinal"})
        if actual != expected:
            raise ValueError("runtime case identity or order differs from the sealed plan")


def _build_plan(
    cases: tuple[GenerationCase, ...],
    *,
    dataset: GenerationDatasetBinding,
    contract: StrictOutputContract[BaseModel],
    created_at_utc: str,
    runtime_image_id: str,
    expires_at_utc: str | None,
    purpose: PlanPurpose,
    selection_algorithm: str,
    reasoning_effort: ReasoningEffort,
    budget_cap_usd: str,
    requires_separate_locked_authorization: bool,
) -> LivePlan:
    selected_sha256 = case_set_sha256(cases)
    luna = frozen_luna_configuration(reasoning_effort)
    reservation = reservation_per_case_usd(luna)
    created_at = parse_utc_timestamp(created_at_utc)
    expires_at = (
        parse_utc_timestamp(expires_at_utc)
        if expires_at_utc is not None
        else created_at + timedelta(hours=4)
    )
    payload = LivePlanPayload(
        schema_version="real-live-plan-v1",
        plan_id=f"luna-{purpose}-{selected_sha256[:12]}",
        created_at_utc=created_at_utc,
        expires_at_utc=_format_utc(expires_at),
        purpose=purpose,
        runtime_image_id=runtime_image_id,
        source_dataset=dataset,
        selected_case_set_sha256=selected_sha256,
        selection_algorithm=selection_algorithm,
        output_contract=freeze_output_contract(contract),
        luna=luna,
        execution_implementation=frozen_execution_implementation(),
        pricing=frozen_pricing(),
        reservation_input_tokens=MAX_INPUT_TOKENS_RESERVED,
        reservation_output_tokens=luna.max_output_tokens,
        reservation_per_case_usd=money(reservation),
        budget_cap_usd=budget_cap_usd,
        reserved_total_usd=money(reservation * len(cases)),
        requires_separate_locked_authorization=requires_separate_locked_authorization,
        cases=tuple(
            PlannedCase(
                ordinal=index,
                case_id=case.case_id,
                trial_id=case.trial_id,
                document_id=case.document_id,
                criterion_kind=case.criterion_kind.value,
                source_sha256=case.source_sha256,
            )
            for index, case in enumerate(cases, start=1)
        ),
    )
    body = payload.model_dump(mode="json")
    return LivePlan.model_validate({**body, "plan_sha256": canonical_sha256(body)})


def _validate_source_split(
    cases: Sequence[GenerationCase],
    dataset: GenerationDatasetBinding,
    *,
    expected_split: str,
) -> None:
    validate_generation_cases(cases)
    if dataset.split != expected_split:
        raise ValueError(f"dataset split must be {expected_split}")
    if dataset.case_count != len(cases):
        raise ValueError("dataset case count does not match source cases")
    if dataset.case_set_sha256 != case_set_sha256(cases):
        raise ValueError("dataset case-set hash does not match source cases")


def _rank(namespace: str, value: str) -> str:
    payload = f"{CANARY_SELECTION_SEED}\0{namespace}\0{value}".encode()
    return hashlib.sha256(payload).hexdigest()


def _stratified_quotas(counts: dict[str, int], *, total: int) -> dict[str, int]:
    nonempty = {name: count for name, count in counts.items() if count > 0}
    if len(nonempty) > total:
        raise ValueError("more non-empty strata than canary slots")
    population = sum(nonempty.values())
    exact = {
        name: Decimal(total) * Decimal(count) / Decimal(population)
        for name, count in nonempty.items()
    }
    quotas = {
        name: max(1, int(value.to_integral_value(rounding=ROUND_FLOOR)))
        for name, value in exact.items()
    }
    while sum(quotas.values()) > total:
        candidates = [name for name, quota in quotas.items() if quota > 1]
        if not candidates:
            raise ValueError("stratified quota minimums exceed canary size")
        selected = min(
            candidates,
            key=lambda name: (exact[name] - quotas[name], name),
        )
        quotas[selected] -= 1
    while sum(quotas.values()) < total:
        selected = max(
            quotas,
            key=lambda name: (exact[name] - quotas[name], name),
        )
        quotas[selected] += 1
    return quotas


def _expected_acknowledgement(plan: LivePlan) -> str:
    if plan.purpose == "development_llf_canary_25":
        return LLF_CANARY_ACKNOWLEDGEMENT
    if plan.purpose == "development_graph_product_canary_25":
        return GRAPH_PRODUCT_CANARY_ACKNOWLEDGEMENT
    return LOCKED_ACKNOWLEDGEMENT


def utc_now() -> datetime:
    return datetime.now(UTC)


def _format_utc(value: datetime) -> str:
    return value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
