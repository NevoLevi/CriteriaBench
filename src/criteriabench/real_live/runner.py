"""Exclusive, atomic, crash-resumable execution of one sealed live plan."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
from collections.abc import Callable, Sequence
from contextlib import AbstractContextManager
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from time import perf_counter
from typing import BinaryIO
from uuid import uuid4

from pydantic import BaseModel

from criteriabench.real_eval.integrity import canonical_sha256
from criteriabench.real_eval.models import GenerationCase
from criteriabench.real_live.contracts import (
    REQUEST_TIMEOUT_SECONDS,
    RESERVATION_PER_CASE_USD,
    AuthorizationClaim,
    AuthorizationClaimPayload,
    AuthorizationConsumption,
    AuthorizationConsumptionPayload,
    CanaryExecutionBinding,
    CaseOutcome,
    CaseOutcomePayload,
    ExternalAttemptClaim,
    ExternalAttemptClaimPayload,
    FailureKind,
    LivePlan,
    PaidAuthorization,
    PendingAttempt,
    PendingAttemptPayload,
    RunSummary,
    RunSummaryPayload,
    SanitizedFailure,
    StrictOutputContract,
    UsageBreakdown,
    caller_execution_identity_sha256,
    freeze_output_contract,
    money,
    parse_utc_timestamp,
    unavailable_usage,
    verify_execution_implementation,
)
from criteriabench.real_live.planning import (
    utc_now,
    verify_authorization,
    verify_authorized_run_directory,
    verify_execution_freshness,
    verify_execution_window_capacity,
    verify_plan_cases,
)
from criteriabench.real_live.transport import (
    StructuredCaller,
    StructuredCallFailure,
    StructuredCallResult,
    StructuredCallSuccess,
    outcome_payload,
    request_sha256,
)

MAX_ARTIFACT_BYTES = 20_000_000
FATAL_FAILURE_KINDS: frozenset[FailureKind] = frozenset(
    {
        "authentication",
        "authorization",
        "budget_breach",
        "model_mismatch",
        "model_not_found",
        "rate_limit",
        "request_configuration",
        "response_contract",
    }
)


class LiveRunError(RuntimeError):
    """A fail-closed live-run guard or artifact integrity error."""


class BudgetGuardError(LiveRunError):
    """The next conservative reservation would exceed the exact sealed cap."""


class FatalProviderConfigurationError(LiveRunError):
    """A sealed fatal outcome stopped the run before another paid attempt."""

    def __init__(self, summary: RunSummary) -> None:
        super().__init__("sealed fatal provider/configuration outcome aborted the live run")
        self.summary = summary


@dataclass(frozen=True, slots=True)
class LiveRunRecovery:
    summary: RunSummary | None
    remaining_case_count: int


def recover_live_run(
    cases: Sequence[GenerationCase],
    *,
    plan: LivePlan,
    authorization: PaidAuthorization,
    execution_binding: CanaryExecutionBinding,
    preregistration_path: Path,
    contract: StrictOutputContract[BaseModel],
    output_dir: Path,
    authorization_state_dir: Path,
    runtime_output_directory_sha256: str,
    host_run_directory_sha256: str,
    authorization_state_directory_sha256: str,
    run_id: str,
    runtime_image_id: str,
    now: datetime,
) -> LiveRunRecovery:
    """Validate/reconcile sealed state without key access, client construction, or calls."""

    verify_authorization(
        plan,
        authorization,
        execution_binding,
        preregistration_path=preregistration_path,
    )
    verify_plan_cases(plan, cases)
    verify_authorized_run_directory(
        authorization,
        runtime_output_directory_sha256,
        host_run_directory_sha256,
        authorization_state_directory_sha256,
        run_id,
    )
    if runtime_image_id != plan.runtime_image_id:
        raise LiveRunError("runtime image ID differs from the exact sealed plan")
    verify_execution_implementation(plan.execution_implementation)
    if freeze_output_contract(contract) != plan.output_contract:
        raise LiveRunError("runtime output contract differs from the exact sealed plan")

    store = RunArtifactStore(output_dir)
    claim_store = AuthorizationClaimStore(authorization_state_dir)
    with store.lock():
        store.initialize(plan, authorization)
        claim = claim_store.preflight(plan, authorization, store)
        if claim is None:
            if store.has_paid_or_terminal_artifacts():
                raise LiveRunError(
                    "paid artifacts exist without the durable authorization claim pair"
                )
            verify_execution_freshness(plan, authorization, now=now)
            verify_execution_window_capacity(
                plan,
                authorization,
                now=now,
                remaining_case_count=len(plan.cases),
            )
            return LiveRunRecovery(summary=None, remaining_case_count=len(plan.cases))

        external_attempt_claims = claim_store.load_attempt_claims(
            plan,
            authorization,
            claim,
        )
        recovered_interrupted = _reconcile_external_attempt_claims(
            store=store,
            plan=plan,
            authorization=authorization,
            cases=cases,
            contract=contract,
            external_attempt_claims=external_attempt_claims,
            outcome_finished_at_utc=_format_utc_timestamp(now),
        )
        outcomes = store.load_outcomes(
            plan,
            authorization,
            cases,
            contract,
            external_attempt_claims,
        )
        stored_summary = store.load_summary(plan, authorization, claim, outcomes)
        if stored_summary is not None:
            return LiveRunRecovery(summary=stored_summary, remaining_case_count=0)

        pending = store.load_pending(plan, cases, contract)
        if pending is not None:
            if pending.ordinal in outcomes:
                store.clear_pending()
            else:
                case = cases[pending.ordinal - 1]
                interrupted = _interrupted_outcome(
                    plan,
                    pending,
                    case,
                    external_attempt_claims[pending.ordinal].external_attempt_claim_sha256,
                    outcome_finished_at_utc=_format_utc_timestamp(now),
                )
                store.write_outcome(interrupted)
                store.clear_pending()
                outcomes[pending.ordinal] = interrupted
                recovered_interrupted = True

        _require_contiguous_prefix(outcomes)
        fatal = _first_fatal_outcome(outcomes)
        if fatal is not None:
            summary = _seal_summary(
                plan,
                authorization,
                claim,
                tuple(outcomes[index] for index in sorted(outcomes)),
                terminal_state="aborted",
                abort_reason=fatal.failure.kind if fatal.failure is not None else None,
            )
            store.write_summary(summary)
            return LiveRunRecovery(summary=summary, remaining_case_count=0)

        remaining = len(plan.cases) - len(outcomes)
        if remaining == 0:
            summary = _seal_summary(
                plan,
                authorization,
                claim,
                tuple(outcomes[index] for index in range(1, len(plan.cases) + 1)),
                terminal_state="completed",
                abort_reason=None,
            )
            store.write_summary(summary)
            return LiveRunRecovery(summary=summary, remaining_case_count=0)

        try:
            verify_execution_freshness(plan, authorization, now=now)
            verify_execution_window_capacity(
                plan,
                authorization,
                now=now,
                remaining_case_count=remaining,
            )
        except ValueError:
            if not outcomes:
                raise
            summary = _seal_summary(
                plan,
                authorization,
                claim,
                tuple(outcomes[index] for index in sorted(outcomes)),
                terminal_state="aborted",
                abort_reason=("interrupted_unknown" if recovered_interrupted else "authorization"),
            )
            store.write_summary(summary)
            return LiveRunRecovery(summary=summary, remaining_case_count=0)
        return LiveRunRecovery(summary=None, remaining_case_count=remaining)


async def run_live_plan(
    cases: Sequence[GenerationCase],
    *,
    plan: LivePlan,
    authorization: PaidAuthorization,
    execution_binding: CanaryExecutionBinding,
    preregistration_path: Path,
    contract: StrictOutputContract[BaseModel],
    caller: StructuredCaller,
    output_dir: Path,
    authorization_state_dir: Path,
    runtime_output_directory_sha256: str,
    host_run_directory_sha256: str,
    authorization_state_directory_sha256: str,
    run_id: str,
    runtime_image_id: str,
    clock: Callable[[], datetime] = utc_now,
) -> RunSummary:
    """Run sequentially, sealing one outcome before advancing to the next case."""

    # Reproduce the exact public chain before interacting even with the caller's
    # configuration identity.  Recovery repeats this guard at its own public API
    # boundary before constructing an artifact store.
    verify_authorization(
        plan,
        authorization,
        execution_binding,
        preregistration_path=preregistration_path,
    )
    _verify_caller_identity(plan, caller)
    recovery_now = clock()
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
        host_run_directory_sha256=host_run_directory_sha256,
        authorization_state_directory_sha256=authorization_state_directory_sha256,
        run_id=run_id,
        runtime_image_id=runtime_image_id,
        now=recovery_now,
    )
    if recovery.summary is not None:
        if recovery.summary.terminal_state == "aborted":
            raise FatalProviderConfigurationError(recovery.summary)
        return recovery.summary
    verify_execution_freshness(plan, authorization, now=recovery_now)
    verify_execution_window_capacity(
        plan,
        authorization,
        now=recovery_now,
        remaining_case_count=recovery.remaining_case_count,
    )

    store = RunArtifactStore(output_dir)
    claim_store = AuthorizationClaimStore(authorization_state_dir)
    with store.lock():
        store.initialize(plan, authorization)
        claim = claim_store.claim_and_consume(plan, authorization, store)
        external_attempt_claims = claim_store.load_attempt_claims(
            plan,
            authorization,
            claim,
        )
        outcomes = store.load_outcomes(
            plan,
            authorization,
            cases,
            contract,
            external_attempt_claims,
        )
        stored_summary = store.load_summary(plan, authorization, claim, outcomes)
        if stored_summary is not None:
            if stored_summary.terminal_state == "aborted":
                raise FatalProviderConfigurationError(stored_summary)
            return stored_summary
        pending = store.load_pending(plan, cases, contract)
        if pending is not None:
            if pending.ordinal in outcomes:
                store.clear_pending()
            else:
                case = cases[pending.ordinal - 1]
                interrupted = _interrupted_outcome(
                    plan,
                    pending,
                    case,
                    external_attempt_claims[pending.ordinal].external_attempt_claim_sha256,
                    outcome_finished_at_utc=_format_utc_timestamp(clock()),
                )
                store.write_outcome(interrupted)
                store.clear_pending()
                outcomes[pending.ordinal] = interrupted

        _require_contiguous_prefix(outcomes)
        existing_fatal = _first_fatal_outcome(outcomes)
        if existing_fatal is not None:
            existing_failure = existing_fatal.failure
            if existing_failure is None:
                raise LiveRunError("fatal outcome scan returned a completed outcome")
            ordered_partial = tuple(outcomes[index] for index in sorted(outcomes))
            aborted = _seal_summary(
                plan,
                authorization,
                claim,
                ordered_partial,
                terminal_state="aborted",
                abort_reason=existing_failure.kind,
            )
            store.write_summary(aborted)
            raise FatalProviderConfigurationError(aborted)
        for planned, case in zip(plan.cases, cases, strict=True):
            if planned.ordinal in outcomes:
                continue
            charged = sum(
                (Decimal(outcome.charged_cost_usd) for outcome in outcomes.values()),
                start=Decimal(0),
            )
            if charged + RESERVATION_PER_CASE_USD > Decimal(plan.budget_cap_usd):
                raise BudgetGuardError(
                    "next case reservation would exceed the exact authorized budget cap"
                )
            request_digest = request_sha256(case, contract)
            call_started_at = clock()
            verify_execution_freshness(plan, authorization, now=call_started_at)
            verify_execution_window_capacity(
                plan,
                authorization,
                now=call_started_at,
                remaining_case_count=len(plan.cases) - len(outcomes),
            )
            verify_execution_implementation(plan.execution_implementation)
            _verify_caller_identity(plan, caller)
            pending_attempt = _seal_pending(
                plan,
                planned.ordinal,
                case,
                attempt_started_at_utc=_format_utc_timestamp(call_started_at),
                request_digest=request_digest,
            )
            external_attempt_claim = claim_store.claim_attempt(
                plan,
                authorization,
                claim,
                pending_attempt,
            )
            store.write_pending(pending_attempt)
            started = perf_counter()
            try:
                async with asyncio.timeout(REQUEST_TIMEOUT_SECONDS):
                    result = await caller.call(case, contract)
                if not isinstance(result, (StructuredCallSuccess, StructuredCallFailure)):
                    result = _runner_failure(type(result).__name__)
            except TimeoutError:
                result = _runner_timeout_failure()
            except Exception as error:
                result = _runner_failure(type(error).__name__)
            latency_ms = max(0, round((perf_counter() - started) * 1_000))
            outcome_finished_at_utc = _format_utc_timestamp(clock())
            try:
                payload = outcome_payload(
                    plan_sha256=plan.plan_sha256,
                    ordinal=planned.ordinal,
                    case=case,
                    request_digest=request_digest,
                    attempt_digest=pending_attempt.pending_sha256,
                    external_attempt_claim_digest=(
                        external_attempt_claim.external_attempt_claim_sha256
                    ),
                    outcome_finished_at_utc=outcome_finished_at_utc,
                    total_latency_ms=latency_ms,
                    result=result,
                )
            except Exception as error:
                payload = _internal_integrity_payload(
                    plan_sha256=plan.plan_sha256,
                    ordinal=planned.ordinal,
                    case=case,
                    request_digest=request_digest,
                    attempt_digest=pending_attempt.pending_sha256,
                    external_attempt_claim_digest=(
                        external_attempt_claim.external_attempt_claim_sha256
                    ),
                    outcome_finished_at_utc=outcome_finished_at_utc,
                    total_latency_ms=latency_ms,
                    result=result,
                    safe_type_name=type(error).__name__,
                )
            payload = _reject_duplicate_response_id(payload, tuple(outcomes.values()))
            outcome = _seal_outcome(payload)
            _verify_attempt_outcome_chronology(
                plan,
                authorization,
                attempt=pending_attempt,
                outcome=outcome,
            )
            store.write_outcome(outcome)
            store.clear_pending()
            outcomes[planned.ordinal] = outcome
            if outcome.failure is not None and outcome.failure.kind in FATAL_FAILURE_KINDS:
                ordered_partial = tuple(outcomes[index] for index in sorted(outcomes))
                aborted = _seal_summary(
                    plan,
                    authorization,
                    claim,
                    ordered_partial,
                    terminal_state="aborted",
                    abort_reason=outcome.failure.kind,
                )
                store.write_summary(aborted)
                raise FatalProviderConfigurationError(aborted)

        ordered = tuple(outcomes[index] for index in range(1, len(plan.cases) + 1))
        summary = _seal_summary(
            plan,
            authorization,
            claim,
            ordered,
            terminal_state="completed",
            abort_reason=None,
        )
        store.write_summary(summary)
        return summary


class RunArtifactStore:
    """Direct-child artifact store protected by one process-level file lock."""

    def __init__(self, root: Path) -> None:
        self.root = root.resolve()

    def lock(self) -> ExclusiveRunLock:
        self.root.mkdir(parents=True, exist_ok=True)
        return ExclusiveRunLock(self.root / ".real-live.lock")

    def initialize(self, plan: LivePlan, authorization: PaidAuthorization) -> None:
        self._write_once_or_match(self.root / "plan.json", _model_bytes(plan))
        self._write_once_or_match(
            self.root / "authorization.json",
            _model_bytes(authorization),
        )

    def load_consumption(self) -> AuthorizationConsumption | None:
        path = self.root / "authorization-consumed.json"
        return _read_exact_model(path, AuthorizationConsumption) if path.exists() else None

    def write_consumption(self, consumption: AuthorizationConsumption) -> None:
        self._write_once_or_match(
            self.root / "authorization-consumed.json",
            _model_bytes(consumption),
        )

    def has_paid_or_terminal_artifacts(self) -> bool:
        fixed = (
            "authorization-consumed.json",
            "pending.json",
            "summary.json",
        )
        return (
            any((self.root / name).exists() for name in fixed)
            or any(self.root.glob("attempt-*.json"))
            or any(self.root.glob("case-*.json"))
        )

    def load_outcomes(
        self,
        plan: LivePlan,
        authorization: PaidAuthorization,
        cases: Sequence[GenerationCase],
        contract: StrictOutputContract[BaseModel],
        external_attempt_claims: dict[int, ExternalAttemptClaim],
    ) -> dict[int, CaseOutcome]:
        outcomes: dict[int, CaseOutcome] = {}
        expected_names = {f"case-{ordinal:04d}.json" for ordinal in range(1, len(plan.cases) + 1)}
        for path in self.root.glob("case-*.json"):
            if path.name not in expected_names or path.is_symlink():
                raise LiveRunError("run directory contains an unexpected case artifact")
            outcome = _read_exact_model(path, CaseOutcome)
            filename_ordinal = int(path.stem.removeprefix("case-"))
            if filename_ordinal != outcome.ordinal:
                raise LiveRunError("case artifact filename differs from its sealed ordinal")
            if outcome.plan_sha256 != plan.plan_sha256:
                raise LiveRunError("case outcome belongs to a different plan")
            planned = plan.cases[outcome.ordinal - 1]
            identity = (
                outcome.case_id,
                outcome.trial_id,
                outcome.document_id,
                outcome.source_sha256,
            )
            expected_identity = (
                planned.case_id,
                planned.trial_id,
                planned.document_id,
                planned.source_sha256,
            )
            if identity != expected_identity or outcome.ordinal in outcomes:
                raise LiveRunError("case outcome identity or ordinal is invalid")
            if outcome.request_sha256 != request_sha256(cases[outcome.ordinal - 1], contract):
                raise LiveRunError("case outcome request hash differs from the current sealed case")
            attempt_path = self.root / f"attempt-{outcome.ordinal:04d}.json"
            if not attempt_path.is_file() or attempt_path.is_symlink():
                raise LiveRunError("case outcome is missing its append-only attempt artifact")
            attempt = self._load_attempt(attempt_path, plan)
            external_attempt_claim = external_attempt_claims.get(outcome.ordinal)
            if external_attempt_claim is None:
                raise LiveRunError("case outcome has no external paid-attempt claim")
            if (
                outcome.attempt_sha256 != attempt.pending_sha256
                or outcome.request_sha256 != attempt.request_sha256
                or outcome.external_attempt_claim_sha256
                != external_attempt_claim.external_attempt_claim_sha256
            ):
                raise LiveRunError("case outcome differs from its sealed paid attempt")
            _verify_attempt_outcome_chronology(
                plan,
                authorization,
                attempt=attempt,
                outcome=outcome,
            )
            outcomes[outcome.ordinal] = outcome
        if set(outcomes) != set(external_attempt_claims):
            raise LiveRunError("local outcomes and external attempt claims differ")
        return outcomes

    def load_pending(
        self,
        plan: LivePlan,
        cases: Sequence[GenerationCase],
        contract: StrictOutputContract[BaseModel],
    ) -> PendingAttempt | None:
        path = self.root / "pending.json"
        current = self._load_attempt(path, plan) if path.exists() else None
        attempts = self.load_attempts(plan, cases, contract)
        dangling: list[PendingAttempt] = []
        for attempt in attempts.values():
            if not (self.root / f"case-{attempt.ordinal:04d}.json").exists():
                dangling.append(attempt)
        if len(dangling) > 1:
            raise LiveRunError("run directory contains multiple unsealed paid attempts")
        if current is not None:
            matching = [
                attempt for attempt in attempts.values() if attempt.ordinal == current.ordinal
            ]
            if len(matching) != 1 or current != matching[0]:
                raise LiveRunError("pending attempt differs from the append-only attempt ledger")
            if (self.root / f"case-{current.ordinal:04d}.json").exists():
                if dangling:
                    raise LiveRunError(
                        "completed pending pointer conflicts with another dangling paid attempt"
                    )
                return current
            if len(dangling) != 1 or current != dangling[0]:
                raise LiveRunError("pending attempt is not the sole dangling paid attempt")
        return dangling[0] if dangling else current

    def load_attempts(
        self,
        plan: LivePlan,
        cases: Sequence[GenerationCase],
        contract: StrictOutputContract[BaseModel],
    ) -> dict[int, PendingAttempt]:
        """Load and bind every local append-only attempt artifact."""

        attempts: dict[int, PendingAttempt] = {}
        expected_names = {
            f"attempt-{ordinal:04d}.json" for ordinal in range(1, len(plan.cases) + 1)
        }
        for attempt_path in self.root.glob("attempt-*.json"):
            if attempt_path.name not in expected_names:
                raise LiveRunError("run directory contains an unexpected attempt artifact")
            attempt = self._load_attempt(attempt_path, plan)
            filename_ordinal = int(attempt_path.stem.removeprefix("attempt-"))
            if filename_ordinal != attempt.ordinal or attempt.ordinal in attempts:
                raise LiveRunError("attempt artifact filename/ordinal is invalid")
            if attempt.request_sha256 != request_sha256(cases[attempt.ordinal - 1], contract):
                raise LiveRunError("attempt request hash differs from the current sealed case")
            attempts[attempt.ordinal] = attempt
        return attempts

    def write_pending(self, pending: PendingAttempt) -> None:
        path = self.root / "pending.json"
        if path.exists():
            raise LiveRunError("a paid attempt is already pending")
        self._write_once_or_match(
            self.root / f"attempt-{pending.ordinal:04d}.json",
            _model_bytes(pending),
        )
        _atomic_write(path, _model_bytes(pending))

    def write_attempt_from_external_claim(self, pending: PendingAttempt) -> None:
        self._write_once_or_match(
            self.root / f"attempt-{pending.ordinal:04d}.json",
            _model_bytes(pending),
        )

    def clear_pending(self) -> None:
        path = self.root / "pending.json"
        if path.exists():
            path.unlink()

    def write_outcome(self, outcome: CaseOutcome) -> None:
        path = self.root / f"case-{outcome.ordinal:04d}.json"
        if path.exists():
            raise LiveRunError("refusing to overwrite a sealed case outcome")
        _atomic_write(path, _model_bytes(outcome))

    def write_summary(self, summary: RunSummary) -> None:
        self._write_once_or_match(self.root / "summary.json", _model_bytes(summary))

    def load_summary(
        self,
        plan: LivePlan,
        authorization: PaidAuthorization,
        claim: AuthorizationClaim,
        outcomes: dict[int, CaseOutcome],
    ) -> RunSummary | None:
        path = self.root / "summary.json"
        if not path.exists():
            return None
        if path.is_symlink():
            raise LiveRunError("summary artifact cannot be a symbolic link")
        summary = _read_exact_model(path, RunSummary)
        if summary.plan_sha256 != plan.plan_sha256:
            raise LiveRunError("summary belongs to a different plan")
        if summary.authorization_sha256 != authorization.authorization_sha256:
            raise LiveRunError("summary belongs to a different authorization")
        ordered = tuple(outcomes[index] for index in sorted(outcomes))
        try:
            expected = _seal_summary(
                plan,
                authorization,
                claim,
                ordered,
                terminal_state=summary.terminal_state,
                abort_reason=summary.abort_reason,
            )
        except Exception as error:
            raise LiveRunError(
                "summary does not exactly derive from the sealed outcomes"
            ) from error
        if summary != expected:
            raise LiveRunError("summary does not exactly derive from the sealed outcomes")
        return summary

    @staticmethod
    def _load_attempt(path: Path, plan: LivePlan) -> PendingAttempt:
        if path.is_symlink():
            raise LiveRunError("attempt artifact cannot be a symbolic link")
        pending = _read_exact_model(path, PendingAttempt)
        if pending.plan_sha256 != plan.plan_sha256 or pending.ordinal > len(plan.cases):
            raise LiveRunError("attempt artifact does not belong to the sealed plan")
        planned = plan.cases[pending.ordinal - 1]
        if pending.case_id != planned.case_id or pending.source_sha256 != planned.source_sha256:
            raise LiveRunError("attempt identity differs from the sealed plan")
        return pending

    @staticmethod
    def _write_once_or_match(path: Path, payload: bytes) -> None:
        if path.exists():
            if path.is_symlink() or path.read_bytes() != payload:
                raise LiveRunError("existing sealed run artifact differs from requested bytes")
            return
        _atomic_write(path, payload)


class AuthorizationClaimStore:
    """Durable one-time authorization claims stored outside the run output tree."""

    def __init__(self, root: Path) -> None:
        if root.is_symlink():
            raise LiveRunError("authorization state directory cannot be a symbolic link")
        try:
            resolved = root.resolve(strict=True)
        except OSError as error:
            raise LiveRunError("authorization state directory must already exist") from error
        if not resolved.is_dir():
            raise LiveRunError("authorization state path must be a directory")
        self.root = resolved

    def preflight(
        self,
        plan: LivePlan,
        authorization: PaidAuthorization,
        store: RunArtifactStore,
    ) -> AuthorizationClaim | None:
        claim = self._load_claim(authorization)
        consumption = store.load_consumption()
        if claim is None and any(
            self.root.glob(f"attempt-{authorization.authorization_sha256}-*.json")
        ):
            raise LiveRunError(
                "external paid-attempt claims exist without their authorization claim"
            )
        self._verify_pair(plan, authorization, store, claim, consumption)
        return claim

    def claim_and_consume(
        self,
        plan: LivePlan,
        authorization: PaidAuthorization,
        store: RunArtifactStore,
    ) -> AuthorizationClaim:
        claim = self._load_claim(authorization)
        consumption = store.load_consumption()
        if claim is None and any(
            self.root.glob(f"attempt-{authorization.authorization_sha256}-*.json")
        ):
            raise LiveRunError(
                "external paid-attempt claims exist without their authorization claim"
            )
        self._verify_pair(plan, authorization, store, claim, consumption)
        if claim is not None:
            return claim

        claim = _seal_authorization_claim(plan, authorization)
        try:
            with self._claim_path(authorization).open("xb") as file:
                file.write(_model_bytes(claim))
                file.flush()
                os.fsync(file.fileno())
        except FileExistsError as error:
            raise LiveRunError("authorization was concurrently or previously claimed") from error

        consumption = _seal_authorization_consumption(plan, authorization, claim)
        store.write_consumption(consumption)
        return claim

    def load_attempt_claims(
        self,
        plan: LivePlan,
        authorization: PaidAuthorization,
        claim: AuthorizationClaim,
    ) -> dict[int, ExternalAttemptClaim]:
        prefix = f"attempt-{authorization.authorization_sha256}-"
        attempt_claims: dict[int, ExternalAttemptClaim] = {}
        for path in self.root.glob(f"{prefix}*.json"):
            if path.is_symlink() or not path.is_file():
                raise LiveRunError("external attempt claim must be a regular file")
            suffix = path.name.removeprefix(prefix).removesuffix(".json")
            if len(suffix) != 4 or not suffix.isdigit():
                raise LiveRunError("external attempt claim filename is invalid")
            ordinal = int(suffix)
            attempt_claim = _read_exact_model(path, ExternalAttemptClaim)
            if ordinal != attempt_claim.ordinal or ordinal in attempt_claims:
                raise LiveRunError("external attempt claim filename/ordinal is invalid")
            expected = _seal_external_attempt_claim(
                plan,
                authorization,
                claim,
                attempt_claim.pending,
            )
            if attempt_claim != expected:
                raise LiveRunError("external attempt claim differs from the sealed run")
            attempt_claims[ordinal] = attempt_claim
        if sorted(attempt_claims) != list(range(1, len(attempt_claims) + 1)):
            raise LiveRunError("external attempt claims must form a contiguous prefix")
        if len(attempt_claims) > len(plan.cases):
            raise LiveRunError("external attempt claim count exceeds the sealed plan")
        return attempt_claims

    def claim_attempt(
        self,
        plan: LivePlan,
        authorization: PaidAuthorization,
        claim: AuthorizationClaim,
        pending: PendingAttempt,
    ) -> ExternalAttemptClaim:
        existing = self.load_attempt_claims(plan, authorization, claim)
        if pending.ordinal != len(existing) + 1:
            raise LiveRunError("next external attempt claim ordinal is not contiguous")
        attempt_claim = _seal_external_attempt_claim(
            plan,
            authorization,
            claim,
            pending,
        )
        path = self._attempt_claim_path(authorization, pending.ordinal)
        try:
            with path.open("xb") as file:
                file.write(_model_bytes(attempt_claim))
                file.flush()
                os.fsync(file.fileno())
        except FileExistsError as error:
            raise LiveRunError("paid attempt ordinal was already externally claimed") from error
        return attempt_claim

    def _load_claim(self, authorization: PaidAuthorization) -> AuthorizationClaim | None:
        path = self._claim_path(authorization)
        if path.is_symlink():
            raise LiveRunError("authorization claim cannot be a symbolic link")
        if not path.exists():
            return None
        if not path.is_file():
            raise LiveRunError("authorization claim must be a regular file")
        return _read_exact_model(path, AuthorizationClaim)

    def _claim_path(self, authorization: PaidAuthorization) -> Path:
        return self.root / f"claim-{authorization.authorization_sha256}.json"

    def _attempt_claim_path(
        self,
        authorization: PaidAuthorization,
        ordinal: int,
    ) -> Path:
        return self.root / f"attempt-{authorization.authorization_sha256}-{ordinal:04d}.json"

    @staticmethod
    def _verify_pair(
        plan: LivePlan,
        authorization: PaidAuthorization,
        store: RunArtifactStore,
        claim: AuthorizationClaim | None,
        consumption: AuthorizationConsumption | None,
    ) -> None:
        if (claim is None) != (consumption is None):
            raise LiveRunError(
                "external authorization claim and local consumption must both exist "
                "or both be absent"
            )
        if claim is None or consumption is None:
            return
        expected_claim = _seal_authorization_claim(plan, authorization)
        if claim != expected_claim:
            raise LiveRunError("external authorization claim differs from this run")
        expected_consumption = _seal_authorization_consumption(
            plan,
            authorization,
            claim,
        )
        if consumption != expected_consumption:
            raise LiveRunError("local authorization consumption differs from its durable claim")


def verify_authorization_claim_state(
    *,
    plan: LivePlan,
    authorization: PaidAuthorization,
    output_dir: Path,
    authorization_state_dir: Path,
) -> None:
    """Read-only preflight used before the CLI touches key material or constructs a client."""

    AuthorizationClaimStore(authorization_state_dir).preflight(
        plan,
        authorization,
        RunArtifactStore(output_dir),
    )


def _reconcile_external_attempt_claims(
    *,
    store: RunArtifactStore,
    plan: LivePlan,
    authorization: PaidAuthorization,
    cases: Sequence[GenerationCase],
    contract: StrictOutputContract[BaseModel],
    external_attempt_claims: dict[int, ExternalAttemptClaim],
    outcome_finished_at_utc: str,
) -> bool:
    """Make the external high-water ledger authoritative after any local rollback.

    Every durable paid-attempt claim is recreated locally if needed and receives
    a conservative interrupted outcome when no sealed result remains.  A claimed
    ordinal is therefore never eligible for another provider call.
    """

    previous_started_at: datetime | None = None
    finished_at = parse_utc_timestamp(outcome_finished_at_utc)
    for ordinal in sorted(external_attempt_claims):
        attempt_claim = external_attempt_claims[ordinal]
        pending = attempt_claim.pending
        case = cases[ordinal - 1]
        if (
            pending.case_id != case.case_id
            or pending.source_sha256 != case.source_sha256
            or pending.request_sha256 != request_sha256(case, contract)
        ):
            raise LiveRunError("external paid-attempt claim differs from its sealed case")
        started_at = parse_utc_timestamp(pending.attempt_started_at_utc)
        try:
            verify_execution_freshness(plan, authorization, now=started_at)
        except ValueError as error:
            raise LiveRunError("external paid-attempt claim began outside a live window") from error
        if previous_started_at is not None and started_at < previous_started_at:
            raise LiveRunError("external paid-attempt timestamps are not monotonic")
        if finished_at < started_at:
            raise LiveRunError("recovery timestamp predates an external paid attempt")
        previous_started_at = started_at
        store.write_attempt_from_external_claim(pending)

    attempts = store.load_attempts(plan, cases, contract)
    if set(attempts) != set(external_attempt_claims):
        raise LiveRunError("local and external paid-attempt inventories differ")
    for ordinal, pending in attempts.items():
        if pending != external_attempt_claims[ordinal].pending:
            raise LiveRunError("local paid attempt differs from its external claim")

    recovered = False
    for ordinal in sorted(external_attempt_claims):
        outcome_path = store.root / f"case-{ordinal:04d}.json"
        if outcome_path.exists():
            continue
        claim = external_attempt_claims[ordinal]
        interrupted = _interrupted_outcome(
            plan,
            claim.pending,
            cases[ordinal - 1],
            claim.external_attempt_claim_sha256,
            outcome_finished_at_utc=outcome_finished_at_utc,
        )
        store.write_outcome(interrupted)
        recovered = True
    return recovered


def _verify_attempt_outcome_chronology(
    plan: LivePlan,
    authorization: PaidAuthorization,
    *,
    attempt: PendingAttempt,
    outcome: CaseOutcome,
) -> None:
    """Verify bounded host-clock chronology for one sealed paid attempt."""

    started_at = parse_utc_timestamp(attempt.attempt_started_at_utc)
    finished_at = parse_utc_timestamp(outcome.outcome_finished_at_utc)
    try:
        verify_execution_freshness(plan, authorization, now=started_at)
    except ValueError as error:
        raise LiveRunError("paid attempt began outside the sealed live windows") from error
    if finished_at < started_at:
        raise LiveRunError("case outcome predates its paid attempt")

    if outcome.failure is not None and outcome.failure.kind == "interrupted_unknown":
        if outcome.total_latency_ms is not None:
            raise LiveRunError("interrupted outcome cannot claim an observed latency")
        return
    if outcome.total_latency_ms is None:
        raise LiveRunError("provider outcome is missing its observed latency")
    timestamp_elapsed_ms = round((finished_at - started_at).total_seconds() * 1_000)
    # Both artifact timestamps intentionally use whole UTC seconds.  One second
    # of quantization on each edge plus a small sealing allowance is explicit.
    tolerance_ms = 2_000
    if outcome.total_latency_ms > round(REQUEST_TIMEOUT_SECONDS * 1_000) + tolerance_ms:
        raise LiveRunError("provider outcome exceeds the whole-call timeout boundary")
    if abs(timestamp_elapsed_ms - outcome.total_latency_ms) > tolerance_ms:
        raise LiveRunError("provider latency conflicts with sealed UTC call timestamps")


class ExclusiveRunLock(AbstractContextManager["ExclusiveRunLock"]):
    """Non-blocking OS file lock, automatically released after a crash."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._file: BinaryIO | None = None

    def __enter__(self) -> ExclusiveRunLock:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        file = self.path.open("a+b")
        file.seek(0, os.SEEK_END)
        if file.tell() == 0:
            file.write(b"0")
            file.flush()
        file.seek(0)
        try:
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(file.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(  # type: ignore[attr-defined]
                    file.fileno(),
                    fcntl.LOCK_EX | fcntl.LOCK_NB,  # type: ignore[attr-defined]
                )
        except OSError as error:
            file.close()
            raise LiveRunError("another process already owns this live-run directory") from error
        self._file = file
        return self

    def __exit__(self, exc_type: object, exc_value: object, traceback: object) -> None:
        if self._file is None:
            return
        file = self._file
        try:
            file.seek(0)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(file.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(  # type: ignore[attr-defined]
                    file.fileno(),
                    fcntl.LOCK_UN,  # type: ignore[attr-defined]
                )
        finally:
            file.close()
            self._file = None


def _seal_pending(
    plan: LivePlan,
    ordinal: int,
    case: GenerationCase,
    *,
    attempt_started_at_utc: str,
    request_digest: str,
) -> PendingAttempt:
    payload = PendingAttemptPayload(
        schema_version="real-live-pending-v1",
        plan_sha256=plan.plan_sha256,
        ordinal=ordinal,
        case_id=case.case_id,
        source_sha256=case.source_sha256,
        request_sha256=request_digest,
        attempt_started_at_utc=attempt_started_at_utc,
        reservation_usd=money(RESERVATION_PER_CASE_USD),
    )
    body = payload.model_dump(mode="json")
    return PendingAttempt.model_validate({**body, "pending_sha256": canonical_sha256(body)})


def _seal_authorization_claim(
    plan: LivePlan,
    authorization: PaidAuthorization,
) -> AuthorizationClaim:
    payload = AuthorizationClaimPayload(
        schema_version="real-live-authorization-claim-v1",
        plan_sha256=plan.plan_sha256,
        preregistration_sha256=authorization.preregistration_sha256,
        preregistration_artifact_sha256=(authorization.preregistration_artifact_sha256),
        execution_binding_sha256=authorization.execution_binding_sha256,
        execution_binding_artifact_sha256=(authorization.execution_binding_artifact_sha256),
        authorization_sha256=authorization.authorization_sha256,
        run_directory_sha256=authorization.run_directory_sha256,
        host_run_directory_sha256=authorization.host_run_directory_sha256,
        authorization_state_directory_sha256=(authorization.authorization_state_directory_sha256),
        authorization_claim_filename=(f"claim-{authorization.authorization_sha256}.json"),
        run_id=authorization.run_id,
    )
    body = payload.model_dump(mode="json")
    return AuthorizationClaim.model_validate({**body, "claim_sha256": canonical_sha256(body)})


def _seal_authorization_consumption(
    plan: LivePlan,
    authorization: PaidAuthorization,
    claim: AuthorizationClaim,
) -> AuthorizationConsumption:
    payload = AuthorizationConsumptionPayload(
        schema_version="real-live-authorization-consumption-v1",
        plan_sha256=plan.plan_sha256,
        authorization_sha256=authorization.authorization_sha256,
        authorization_claim_sha256=claim.claim_sha256,
        run_directory_sha256=authorization.run_directory_sha256,
        run_id=authorization.run_id,
    )
    body = payload.model_dump(mode="json")
    return AuthorizationConsumption.model_validate(
        {**body, "consumption_sha256": canonical_sha256(body)}
    )


def _seal_external_attempt_claim(
    plan: LivePlan,
    authorization: PaidAuthorization,
    authorization_claim: AuthorizationClaim,
    pending: PendingAttempt,
) -> ExternalAttemptClaim:
    payload = ExternalAttemptClaimPayload(
        schema_version="real-live-external-attempt-claim-v1",
        plan_sha256=plan.plan_sha256,
        authorization_sha256=authorization.authorization_sha256,
        authorization_claim_sha256=authorization_claim.claim_sha256,
        preregistration_sha256=authorization.preregistration_sha256,
        execution_binding_sha256=authorization.execution_binding_sha256,
        run_id=authorization.run_id,
        host_run_directory_sha256=authorization.host_run_directory_sha256,
        authorization_state_directory_sha256=(authorization.authorization_state_directory_sha256),
        ordinal=pending.ordinal,
        pending=pending,
    )
    body = payload.model_dump(mode="json")
    return ExternalAttemptClaim.model_validate(
        {**body, "external_attempt_claim_sha256": canonical_sha256(body)}
    )


def _interrupted_outcome(
    plan: LivePlan,
    pending: PendingAttempt,
    case: GenerationCase,
    external_attempt_claim_sha256: str,
    *,
    outcome_finished_at_utc: str,
) -> CaseOutcome:
    failure = SanitizedFailure(
        kind="interrupted_unknown",
        retryable=False,
        fingerprint_sha256=canonical_sha256(
            {"kind": "interrupted_unknown", "pending_sha256": pending.pending_sha256}
        ),
    )
    payload = CaseOutcomePayload(
        schema_version="real-live-case-outcome-v1",
        plan_sha256=plan.plan_sha256,
        ordinal=pending.ordinal,
        case_id=case.case_id,
        trial_id=case.trial_id,
        document_id=case.document_id,
        source_sha256=case.source_sha256,
        request_sha256=pending.request_sha256,
        attempt_sha256=pending.pending_sha256,
        external_attempt_claim_sha256=external_attempt_claim_sha256,
        outcome_finished_at_utc=outcome_finished_at_utc,
        total_latency_ms=None,
        status="failed",
        usage=unavailable_usage(),
        charged_cost_usd=money(RESERVATION_PER_CASE_USD),
        response_id_sha256=None,
        provider_model=None,
        provider_model_sha256=None,
        provider_response_object=None,
        provider_response_object_sha256=None,
        provider_service_tier=None,
        provider_service_tier_sha256=None,
        normalized_output_sha256=None,
        normalized_output=None,
        failure=failure,
    )
    return _seal_outcome(payload)


def _runner_failure(safe_type_name: str) -> StructuredCallFailure:
    return StructuredCallFailure(
        failure=SanitizedFailure(
            kind="response_contract",
            retryable=False,
            fingerprint_sha256=canonical_sha256(
                {
                    "kind": "response_contract",
                    "escaped_caller_failure_type": safe_type_name,
                }
            ),
        ),
        response_id_sha256=None,
        usage=unavailable_usage(),
    )


def _runner_timeout_failure() -> StructuredCallFailure:
    return StructuredCallFailure(
        failure=SanitizedFailure(
            kind="timeout",
            retryable=True,
            fingerprint_sha256=canonical_sha256(
                {"kind": "timeout", "boundary": "app-level-whole-call"}
            ),
        ),
        response_id_sha256=None,
        usage=unavailable_usage(),
    )


def _internal_integrity_payload(
    *,
    plan_sha256: str,
    ordinal: int,
    case: GenerationCase,
    request_digest: str,
    attempt_digest: str,
    external_attempt_claim_digest: str,
    outcome_finished_at_utc: str,
    total_latency_ms: int,
    result: StructuredCallResult[BaseModel],
    safe_type_name: str,
) -> CaseOutcomePayload:
    """Preserve trusted known usage if ancillary outcome conversion fails."""

    usage = result.usage if isinstance(result.usage, UsageBreakdown) else unavailable_usage()
    charged = (
        usage.total_cost_usd
        if usage.availability == "complete"
        else money(RESERVATION_PER_CASE_USD)
    )
    breach = Decimal(charged) > RESERVATION_PER_CASE_USD
    failure = SanitizedFailure(
        kind="budget_breach" if breach else "response_contract",
        retryable=False,
        fingerprint_sha256=canonical_sha256(
            {
                "kind": "budget_breach" if breach else "response_contract",
                "runner_conversion_failure_type": safe_type_name,
            }
        ),
    )
    return CaseOutcomePayload(
        schema_version="real-live-case-outcome-v1",
        plan_sha256=plan_sha256,
        ordinal=ordinal,
        case_id=case.case_id,
        trial_id=case.trial_id,
        document_id=case.document_id,
        source_sha256=case.source_sha256,
        request_sha256=request_digest,
        attempt_sha256=attempt_digest,
        external_attempt_claim_sha256=external_attempt_claim_digest,
        outcome_finished_at_utc=outcome_finished_at_utc,
        total_latency_ms=total_latency_ms,
        status="failed",
        usage=usage,
        charged_cost_usd=charged,
        response_id_sha256=_safe_digest(result.response_id_sha256),
        provider_model=_safe_provider_label(result.provider_model),
        provider_model_sha256=_safe_label_digest(
            result.provider_model,
            result.provider_model_sha256,
        ),
        provider_response_object=_safe_provider_label(result.provider_response_object),
        provider_response_object_sha256=_safe_label_digest(
            result.provider_response_object,
            result.provider_response_object_sha256,
        ),
        provider_service_tier=_safe_provider_label(result.provider_service_tier),
        provider_service_tier_sha256=_safe_label_digest(
            result.provider_service_tier,
            result.provider_service_tier_sha256,
        ),
        normalized_output_sha256=None,
        normalized_output=None,
        failure=failure,
    )


def _safe_digest(value: object) -> str | None:
    if not isinstance(value, str) or len(value) != 64:
        return None
    return value if all(character in "0123456789abcdef" for character in value) else None


def _safe_provider_label(value: object) -> str | None:
    if not isinstance(value, str) or not (1 <= len(value) <= 128):
        return None
    allowed = frozenset("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789._:/-")
    return (
        value if value[0].isalnum() and all(character in allowed for character in value) else None
    )


def _safe_label_digest(label: object, digest: object) -> str | None:
    safe_label = _safe_provider_label(label)
    safe_digest = _safe_digest(digest)
    if safe_label is None or safe_digest != hashlib.sha256(safe_label.encode()).hexdigest():
        return None
    return safe_digest


def _seal_outcome(payload: CaseOutcomePayload) -> CaseOutcome:
    body = payload.model_dump(mode="json")
    return CaseOutcome.model_validate({**body, "outcome_sha256": canonical_sha256(body)})


def _reject_duplicate_response_id(
    payload: CaseOutcomePayload,
    prior_outcomes: Sequence[CaseOutcome],
) -> CaseOutcomePayload:
    response_id_sha256 = payload.response_id_sha256
    if response_id_sha256 is None or all(
        outcome.response_id_sha256 != response_id_sha256 for outcome in prior_outcomes
    ):
        return payload
    if Decimal(payload.charged_cost_usd) > RESERVATION_PER_CASE_USD:
        return payload
    body = payload.model_dump(mode="json")
    return CaseOutcomePayload.model_validate(
        {
            **body,
            "status": "failed",
            "normalized_output_sha256": None,
            "normalized_output": None,
            "failure": SanitizedFailure(
                kind="response_contract",
                retryable=False,
                fingerprint_sha256=canonical_sha256(
                    {"kind": "response_contract", "safe_code": "duplicate_response_id"}
                ),
            ),
        }
    )


def _seal_summary(
    plan: LivePlan,
    authorization: PaidAuthorization,
    claim: AuthorizationClaim,
    outcomes: tuple[CaseOutcome, ...],
    *,
    terminal_state: str,
    abort_reason: FailureKind | None,
) -> RunSummary:
    charged_total = sum(
        (Decimal(outcome.charged_cost_usd) for outcome in outcomes),
        start=Decimal(0),
    )
    if terminal_state == "completed" and charged_total > Decimal(plan.budget_cap_usd):
        raise BudgetGuardError("sealed outcomes exceed the exact authorized budget cap")
    observed_latencies = tuple(
        outcome.total_latency_ms for outcome in outcomes if outcome.total_latency_ms is not None
    )
    external_attempt_claim_hashes = tuple(
        outcome.external_attempt_claim_sha256 for outcome in outcomes
    )
    external_attempt_claim_inventory_sha256 = canonical_sha256(
        {"external_attempt_claim_hashes": external_attempt_claim_hashes}
    )
    payload = RunSummaryPayload(
        schema_version="real-live-summary-v1",
        plan_sha256=plan.plan_sha256,
        authorization_sha256=authorization.authorization_sha256,
        authorization_claim_sha256=claim.claim_sha256,
        preregistration_sha256=authorization.preregistration_sha256,
        preregistration_artifact_sha256=(authorization.preregistration_artifact_sha256),
        execution_binding_sha256=authorization.execution_binding_sha256,
        execution_binding_artifact_sha256=(authorization.execution_binding_artifact_sha256),
        external_attempt_claim_count=len(external_attempt_claim_hashes),
        external_attempt_claim_inventory_sha256=(external_attempt_claim_inventory_sha256),
        execution_implementation_sha256=(plan.execution_implementation.implementation_sha256),
        terminal_state=terminal_state,
        abort_reason=abort_reason,
        case_count=len(plan.cases),
        attempted_count=len(outcomes),
        not_attempted_count=len(plan.cases) - len(outcomes),
        completed_count=sum(outcome.status == "completed" for outcome in outcomes),
        failed_count=sum(outcome.status == "failed" for outcome in outcomes),
        usage_unknown_count=sum(
            outcome.usage.availability == "unavailable" for outcome in outcomes
        ),
        observed_latency_case_count=len(observed_latencies),
        total_latency_ms=sum(observed_latencies),
        budget_cap_usd=plan.budget_cap_usd,
        charged_total_usd=money(charged_total),
        budget_breached=abort_reason == "budget_breach",
        outcome_hashes=tuple(outcome.outcome_sha256 for outcome in outcomes),
    )
    body = payload.model_dump(mode="json")
    return RunSummary.model_validate({**body, "summary_sha256": canonical_sha256(body)})


def _verify_caller_identity(plan: LivePlan, caller: StructuredCaller) -> None:
    expected = caller_execution_identity_sha256(plan.luna, plan.execution_implementation)
    if getattr(caller, "execution_identity_sha256", None) != expected:
        raise LiveRunError("caller execution identity differs from the sealed Luna plan")


def _require_contiguous_prefix(outcomes: dict[int, CaseOutcome]) -> None:
    if sorted(outcomes) != list(range(1, len(outcomes) + 1)):
        raise LiveRunError("existing outcomes are not a contiguous plan prefix")


def _first_fatal_outcome(outcomes: dict[int, CaseOutcome]) -> CaseOutcome | None:
    for ordinal in sorted(outcomes):
        outcome = outcomes[ordinal]
        failure = outcome.failure
        if failure is not None and failure.kind in FATAL_FAILURE_KINDS:
            return outcome
    return None


def _atomic_write(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    try:
        with temporary.open("xb") as file:
            file.write(payload)
            file.flush()
            os.fsync(file.fileno())
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def _model_bytes(model: BaseModel) -> bytes:
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


def _format_utc_timestamp(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise LiveRunError("live-run clock must be timezone-aware")
    return value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _read_exact_model[TModel: BaseModel](
    path: Path,
    model_type: type[TModel],
) -> TModel:
    raw = path.read_bytes()
    if len(raw) > MAX_ARTIFACT_BYTES:
        raise LiveRunError("sealed run artifact exceeds the size limit")
    try:
        model = model_type.model_validate_json(raw)
    except Exception as error:
        raise LiveRunError("sealed run artifact is invalid") from error
    if _model_bytes(model) != raw:
        raise LiveRunError("sealed run artifact is not canonical JSON")
    return model
