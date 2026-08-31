from __future__ import annotations

from types import SimpleNamespace

from criteriabench.contracts import SCHEMA_VERSION, extraction_contract_hash
from criteriabench.domain.schemas import TrialDocument
from criteriabench.queue_reliable import ExtractionJob, QueuedMessage
from criteriabench.worker.processor import process_message


def _trial() -> TrialDocument:
    return TrialDocument(
        trial_id="TEST-001",
        title="Worker test",
        eligibility_text="Inclusion Criteria:\n- Adult",
    )


def _message() -> QueuedMessage:
    provider = "mock"
    model = "deterministic-rules-v1"
    job = ExtractionJob(
        run_id="run-1",
        trial=_trial(),
        provider=provider,
        model=model,
        schema_version=SCHEMA_VERSION,
        contract_hash=extraction_contract_hash(provider=provider, model=model),
    )
    return QueuedMessage(job=job, raw_payload=job.model_dump_json())


class FakeRepository:
    def __init__(self, status: str | None) -> None:
        self.status = status
        self.trial = _trial()

    async def get_extraction(self, run_id: str) -> object | None:
        del run_id
        if self.status is None:
            return None
        return SimpleNamespace(
            status=self.status,
            trial_id=self.trial.trial_id,
            request_json=self.trial.model_dump(mode="json"),
        )


class FakeQueue:
    def __init__(self) -> None:
        self.acknowledged = 0

    async def acknowledge(self, message: QueuedMessage) -> None:
        del message
        self.acknowledged += 1


class FakeService:
    provider = SimpleNamespace(name="mock", model="deterministic-rules-v1")

    def __init__(self, repository: FakeRepository, terminal_status: str | None) -> None:
        self.repository = repository
        self.terminal_status = terminal_status
        self.calls = 0

    async def execute(self, *args: object, **kwargs: object) -> None:
        del args, kwargs
        self.calls += 1
        if self.terminal_status is not None:
            self.repository.status = self.terminal_status
        if self.terminal_status != "completed":
            raise RuntimeError("simulated worker interruption")


async def test_completed_duplicate_is_skipped_and_acknowledged() -> None:
    repository = FakeRepository("completed")
    queue = FakeQueue()
    service = FakeService(repository, "completed")
    result = await process_message(_message(), service=service, repository=repository, queue=queue)
    assert result == "duplicate"
    assert service.calls == 0
    assert queue.acknowledged == 1


async def test_persisted_failure_is_acknowledged() -> None:
    repository = FakeRepository("queued")
    queue = FakeQueue()
    service = FakeService(repository, "failed")
    result = await process_message(_message(), service=service, repository=repository, queue=queue)
    assert result == "failed"
    assert queue.acknowledged == 1


async def test_infrastructure_interruption_remains_recoverable() -> None:
    repository = FakeRepository("queued")
    queue = FakeQueue()
    service = FakeService(repository, None)
    result = await process_message(_message(), service=service, repository=repository, queue=queue)
    assert result == "retry_pending"
    assert queue.acknowledged == 0
