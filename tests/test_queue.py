from __future__ import annotations

from collections import defaultdict

from criteriabench.contracts import SCHEMA_VERSION, extraction_contract_hash
from criteriabench.domain.schemas import TrialDocument
from criteriabench.queue_reliable import ExtractionJob, RedisQueue


class FakeRedis:
    def __init__(self) -> None:
        self.lists: dict[str, list[str]] = defaultdict(list)
        self.closed = False

    async def ping(self) -> bool:
        return True

    async def lpush(self, key: str, value: str) -> int:
        self.lists[key].insert(0, value)
        return len(self.lists[key])

    async def brpoplpush(
        self,
        source: str,
        destination: str,
        **options: int,
    ) -> str | None:
        del options
        if not self.lists[source]:
            return None
        value = self.lists[source].pop()
        self.lists[destination].insert(0, value)
        return value

    async def rpoplpush(self, source: str, destination: str) -> str | None:
        if not self.lists[source]:
            return None
        value = self.lists[source].pop()
        self.lists[destination].insert(0, value)
        return value

    async def lrem(self, key: str, count: int, value: str) -> int:
        assert count == 1
        try:
            self.lists[key].remove(value)
        except ValueError:
            return 0
        return 1

    async def aclose(self) -> None:
        self.closed = True


def _job(run_id: str) -> ExtractionJob:
    provider = "mock"
    model = "deterministic-rules-v1"
    return ExtractionJob(
        run_id=run_id,
        trial=TrialDocument(
            trial_id=run_id,
            title="Queue test",
            eligibility_text="Inclusion Criteria:\n- Adult",
        ),
        provider=provider,
        model=model,
        schema_version=SCHEMA_VERSION,
        contract_hash=extraction_contract_hash(provider=provider, model=model),
    )


async def test_queue_claims_atomically_and_acknowledges() -> None:
    redis = FakeRedis()
    queue = RedisQueue("redis://unused", "jobs", client=redis)
    await queue.enqueue(_job("run-1"))
    message = await queue.dequeue(1)
    assert message is not None
    assert message.job.run_id == "run-1"
    assert redis.lists["jobs"] == []
    assert len(redis.lists["jobs:processing"]) == 1
    await queue.acknowledge(message)
    assert redis.lists["jobs:processing"] == []


async def test_unacknowledged_job_is_recovered_after_worker_crash() -> None:
    redis = FakeRedis()
    first_worker = RedisQueue("redis://unused", "jobs", client=redis)
    await first_worker.enqueue(_job("run-1"))
    claimed = await first_worker.dequeue(1)
    assert claimed is not None

    restarted_worker = RedisQueue("redis://unused", "jobs", client=redis)
    assert await restarted_worker.recover_processing() == 1
    recovered = await restarted_worker.dequeue(1)
    assert recovered is not None
    assert recovered.job.run_id == "run-1"


async def test_queue_preserves_fifo_order() -> None:
    redis = FakeRedis()
    queue = RedisQueue("redis://unused", "jobs", client=redis)
    await queue.enqueue(_job("run-1"))
    await queue.enqueue(_job("run-2"))
    first = await queue.dequeue(1)
    assert first is not None
    assert first.job.run_id == "run-1"
