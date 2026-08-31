from __future__ import annotations

from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from criteriabench.contracts import extraction_contract_hash
from criteriabench.domain.schemas import TrialDocument
from criteriabench.queue_reliable import ExtractionJob, QueuedMessage
from criteriabench.worker.processor import process_message


def _trial() -> TrialDocument:
    return TrialDocument(
        trial_id="TEST-001",
        title="Contract test",
        eligibility_text="Inclusion Criteria:\n- Adult",
    )


def test_queue_job_requires_frozen_extraction_contract() -> None:
    with pytest.raises(ValidationError):
        ExtractionJob(run_id="run-1", trial=_trial())


class FakeRepository:
    def __init__(self) -> None:
        self.status = "queued"
        self.error_code: str | None = None

    async def get_extraction(self, run_id: str) -> object:
        del run_id
        return SimpleNamespace(status=self.status)

    async def mark_failed(self, run_id: str, *, error_code: str) -> None:
        del run_id
        self.status = "failed"
        self.error_code = error_code


class FakeQueue:
    def __init__(self) -> None:
        self.acknowledged = False

    async def acknowledge(self, message: QueuedMessage) -> None:
        del message
        self.acknowledged = True


class WrongWorkerService:
    provider = SimpleNamespace(name="mock", model="deterministic-rules-v2")

    async def execute(self, *args: object, **kwargs: object) -> None:
        raise AssertionError("contract mismatch must fail before extraction")


async def test_worker_fails_closed_when_queued_contract_differs() -> None:
    job = ExtractionJob(
        run_id="run-1",
        trial=_trial(),
        provider="mock",
        model="deterministic-rules-v1",
        schema_version="1.0",
        contract_hash=extraction_contract_hash(
            provider="mock",
            model="deterministic-rules-v1",
        ),
    )
    message = QueuedMessage(job=job, raw_payload=job.model_dump_json())
    repository = FakeRepository()
    queue = FakeQueue()
    result = await process_message(
        message,
        service=WrongWorkerService(),
        repository=repository,
        queue=queue,
    )
    assert result == "config_mismatch"
    assert repository.error_code == "worker_config_mismatch"
    assert queue.acknowledged is True
