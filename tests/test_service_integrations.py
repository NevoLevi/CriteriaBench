"""Opt-in checks against real CI PostgreSQL and Redis services."""

from __future__ import annotations

import os
from uuid import uuid4

import pytest
from redis.asyncio import Redis

from criteriabench.contracts import SCHEMA_VERSION, extraction_contract_hash
from criteriabench.db.repositories import RunRepository
from criteriabench.db.session import Database
from criteriabench.domain.schemas import TrialDocument
from criteriabench.queue_reliable import ExtractionJob, RedisQueue

pytestmark = pytest.mark.integration


def _service_url(name: str) -> str:
    if value := os.getenv(name):
        return value
    if os.getenv("CI"):
        pytest.fail(f"{name} is required for service-integration CI")
    pytest.skip(f"{name} is only set in service-integration CI")


async def test_real_postgres_repository_round_trip() -> None:
    url = _service_url("TEST_POSTGRES_URL")
    database = Database(url)
    await database.initialize()
    repository = RunRepository(database)
    trial_id = f"PG-{uuid4().hex[:12]}"
    try:
        assert await database.ping()
        created = await repository.create_extraction(
            TrialDocument(
                trial_id=trial_id,
                title="PostgreSQL integration",
                eligibility_text="Adults aged 18 years or older",
            ),
            provider="mock",
            model="deterministic-rules-v1",
        )
        stored = await repository.get_extraction(created.id)
        assert stored is not None
        assert stored.request_json["trial_id"] == trial_id
    finally:
        await database.close()


async def test_real_redis_claim_and_ack_round_trip() -> None:
    url = _service_url("TEST_REDIS_URL")
    client: Redis = Redis.from_url(url, decode_responses=True)
    queue_name = f"criteriabench:test:{uuid4().hex}"
    queue = RedisQueue(url, queue_name, client=client)
    provider = "mock"
    model = "deterministic-rules-v1"
    try:
        assert await queue.ping()
        await queue.enqueue(
            ExtractionJob(
                run_id=uuid4().hex,
                trial=TrialDocument(
                    trial_id=f"REDIS-{uuid4().hex[:12]}",
                    title="Redis integration",
                    eligibility_text="Adults",
                ),
                provider=provider,
                model=model,
                schema_version=SCHEMA_VERSION,
                contract_hash=extraction_contract_hash(provider=provider, model=model),
            )
        )
        claimed = await queue.dequeue(2)
        assert claimed is not None
        await queue.acknowledge(claimed)
        assert await client.llen(f"{queue_name}:processing") == 0
    finally:
        await client.delete(queue_name, f"{queue_name}:processing", f"{queue_name}:dead-letter")
        await queue.close()
