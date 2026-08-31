"""Idempotent best-effort processing for at-least-once queue messages."""

from __future__ import annotations

from typing import Any, Literal, Protocol

from pydantic import ValidationError

from criteriabench.contracts import SCHEMA_VERSION, extraction_contract_hash
from criteriabench.db.models import ExtractionRun
from criteriabench.domain.schemas import TrialDocument
from criteriabench.queue_reliable import QueuedMessage


class ProviderIdentity(Protocol):
    @property
    def name(self) -> str: ...

    @property
    def model(self) -> str: ...


class ExtractionExecutor(Protocol):
    @property
    def provider(self) -> ProviderIdentity: ...

    async def execute(
        self,
        trial: TrialDocument,
        *,
        persist: bool = True,
        existing_run_id: str | None = None,
    ) -> Any: ...


class RunLookup(Protocol):
    async def get_extraction(self, run_id: str) -> ExtractionRun | None: ...

    async def mark_failed(self, run_id: str, *, error_code: str) -> None: ...


class MessageAcknowledger(Protocol):
    async def acknowledge(self, message: QueuedMessage) -> None: ...


ProcessResult = Literal[
    "completed",
    "failed",
    "duplicate",
    "orphan",
    "retry_pending",
    "config_mismatch",
    "payload_mismatch",
]


async def process_message(
    message: QueuedMessage,
    *,
    service: ExtractionExecutor,
    repository: RunLookup,
    queue: MessageAcknowledger,
) -> ProcessResult:
    """Process or skip a message, ACKing only after a terminal DB state."""

    current = await repository.get_extraction(message.job.run_id)
    if current is None:
        await queue.acknowledge(message)
        return "orphan"
    if current.status in {"completed", "failed"}:
        await queue.acknowledge(message)
        return "duplicate"

    if not _contract_matches(message, service.provider):
        await repository.mark_failed(
            message.job.run_id,
            error_code="worker_config_mismatch",
        )
        await queue.acknowledge(message)
        return "config_mismatch"

    if not _payload_matches(message, current):
        await repository.mark_failed(
            message.job.run_id,
            error_code="worker_payload_mismatch",
        )
        await queue.acknowledge(message)
        return "payload_mismatch"

    try:
        await service.execute(
            message.job.trial,
            persist=True,
            existing_run_id=message.job.run_id,
        )
    except Exception:
        persisted = await repository.get_extraction(message.job.run_id)
        if persisted is not None and persisted.status == "failed":
            await queue.acknowledge(message)
            return "failed"
        return "retry_pending"
    await queue.acknowledge(message)
    return "completed"


def _contract_matches(message: QueuedMessage, provider: ProviderIdentity) -> bool:
    job = message.job
    if job.provider != provider.name or job.model != provider.model:
        return False
    if job.schema_version != SCHEMA_VERSION:
        return False
    expected_hash = extraction_contract_hash(provider=provider.name, model=provider.model)
    return job.contract_hash == expected_hash


def _payload_matches(message: QueuedMessage, current: ExtractionRun) -> bool:
    job = message.job
    if current.trial_id != job.trial.trial_id:
        return False
    try:
        stored_trial = TrialDocument.model_validate(current.request_json)
    except ValidationError:
        return False
    return stored_trial == job.trial
