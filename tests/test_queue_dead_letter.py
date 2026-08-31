from __future__ import annotations

from criteriabench.queue_reliable import RedisQueue
from tests.test_queue import FakeRedis


async def test_malformed_job_is_dead_lettered_instead_of_replayed_forever() -> None:
    redis = FakeRedis()
    queue = RedisQueue("redis://unused", "jobs", client=redis)
    await redis.lpush("jobs", "{not-valid-json")
    assert await queue.dequeue(1) is None
    assert redis.lists["jobs:processing"] == []
    assert redis.lists["jobs:dead-letter"] == ["{not-valid-json"]
