from __future__ import annotations

from types import SimpleNamespace

from criteriabench.contracts import SCHEMA_VERSION, extraction_contract_hash
from criteriabench.db.models import ExtractionRun
from criteriabench.domain.schemas import TrialDocument
from criteriabench.queue_reliable import ExtractionJob, QueuedMessage
from criteriabench.worker.processor import process_message


class FakeRepository:
    def __init__(self, run: ExtractionRun) -> None:
        self.run = run

    async def get_extraction(self, run_id: str) -> ExtractionRun | None:
        assert run_id == self.run.id
        return self.run

    async def mark_failed(self, run_id: str, *, error_code: str) -> None:
        assert run_id == self.run.id
        self.run.status = "failed"
        self.run.error_code = error_code


class FakeQueue:
    def __init__(self) -> None:
        self.acknowledged = 0

    async def acknowledge(self, message: QueuedMessage) -> None:
        del message
        self.acknowledged += 1


class FailIfCalledService:
    provider = SimpleNamespace(name="mock", model="deterministic-rules-v1")

    def __init__(self) -> None:
        self.calls = 0

    async def execute(self, *args: object, **kwargs: object) -> None:
        del args, kwargs
        self.calls += 1
        raise AssertionError("mismatched queue payload must not execute")


def _message(trial: TrialDocument) -> QueuedMessage:
    provider = "mock"
    model = "deterministic-rules-v1"
    job = ExtractionJob(
        run_id="run-payload-integrity",
        trial=trial,
        provider=provider,
        model=model,
        schema_version=SCHEMA_VERSION,
        contract_hash=extraction_contract_hash(provider=provider, model=model),
    )
    return QueuedMessage(job=job, raw_payload=job.model_dump_json())


async def test_worker_fails_closed_when_queue_trial_differs_from_stored_request() -> None:
    stored_trial = TrialDocument(
        trial_id="TEST-WORKER-001",
        title="Stored trial",
        eligibility_text="Inclusion Criteria:\n- Adult",
    )
    corrupted_trial = stored_trial.model_copy(
        update={"eligibility_text": "Inclusion Criteria:\n- Fabricated replacement"}
    )
    run = ExtractionRun(
        id="run-payload-integrity",
        trial_id=stored_trial.trial_id,
        trial_title=stored_trial.title,
        provider="mock",
        model="deterministic-rules-v1",
        status="queued",
        request_json=stored_trial.model_dump(mode="json"),
    )
    repository = FakeRepository(run)
    queue = FakeQueue()
    service = FailIfCalledService()

    result = await process_message(
        _message(corrupted_trial),
        service=service,
        repository=repository,
        queue=queue,
    )

    assert result == "payload_mismatch"
    assert run.status == "failed"
    assert run.error_code == "worker_payload_mismatch"
    assert service.calls == 0
    assert queue.acknowledged == 1
