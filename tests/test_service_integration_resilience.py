"""Real-service migration and at-least-once queue resilience checks."""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
from pathlib import Path
from uuid import uuid4

import pytest
from redis.asyncio import Redis

from criteriabench.contracts import SCHEMA_VERSION, extraction_contract_hash
from criteriabench.db.repositories import RunRepository
from criteriabench.db.session import Database
from criteriabench.domain.schemas import TrialDocument
from criteriabench.queue_reliable import ExtractionJob, RedisQueue

ROOT = Path(__file__).resolve().parents[1]
pytestmark = pytest.mark.integration


def _service_url(name: str) -> str:
    if value := os.getenv(name):
        return value
    if os.getenv("CI"):
        pytest.fail(f"{name} is required for service-integration CI")
    pytest.skip(f"{name} is only set in service-integration CI")


def _job(run_id: str) -> ExtractionJob:
    provider = "mock"
    model = "deterministic-rules-v1"
    return ExtractionJob(
        run_id=run_id,
        trial=TrialDocument(
            trial_id=f"SERVICE-{run_id[:12]}",
            title="Service resilience integration",
            eligibility_text="Adults",
        ),
        provider=provider,
        model=model,
        schema_version=SCHEMA_VERSION,
        contract_hash=extraction_contract_hash(provider=provider, model=model),
    )


def test_real_postgres_alembic_upgrade_downgrade_and_repository() -> None:
    url = _service_url("TEST_POSTGRES_URL")
    process_environment = os.environ.copy()
    process_environment["DATABASE_URL"] = url
    base_command = [
        sys.executable,
        "-m",
        "alembic",
        "-c",
        "migrations/alembic.ini",
    ]
    for operation in (("upgrade", "head"), ("downgrade", "base"), ("upgrade", "head")):
        subprocess.run(
            [*base_command, *operation],
            cwd=ROOT,
            env=process_environment,
            check=True,
            capture_output=True,
            text=True,
            timeout=60,
        )

    async def repository_round_trip() -> None:
        database = Database(url)
        repository = RunRepository(database)
        try:
            assert await database.ping()
            run = await repository.create_extraction(
                TrialDocument(
                    trial_id=f"PG-{uuid4().hex[:12]}",
                    title="Alembic-managed PostgreSQL",
                    eligibility_text="Adults aged 18 years or older",
                ),
                provider="mock",
                model="deterministic-rules-v1",
            )
            assert await repository.get_extraction(run.id) is not None
        finally:
            await database.close()

    asyncio.run(repository_round_trip())


async def test_real_redis_recovery_and_dead_letter() -> None:
    url = _service_url("TEST_REDIS_URL")
    client: Redis = Redis.from_url(url, decode_responses=True)
    queue_name = f"criteriabench:resilience:{uuid4().hex}"
    first_worker = RedisQueue(url, queue_name, client=client)
    replacement_worker = RedisQueue(url, queue_name, client=client)
    try:
        await first_worker.enqueue(_job(uuid4().hex))
        claimed = await first_worker.dequeue(2)
        assert claimed is not None
        assert await replacement_worker.recover_processing() == 1
        recovered = await replacement_worker.dequeue(2)
        assert recovered is not None
        await replacement_worker.acknowledge(recovered)

        await client.lpush(queue_name, "not-a-valid-envelope")
        assert await replacement_worker.dequeue(2) is None
        assert await client.llen(f"{queue_name}:processing") == 0
        assert await client.llen(f"{queue_name}:dead-letter") == 1
    finally:
        await client.delete(queue_name, f"{queue_name}:processing", f"{queue_name}:dead-letter")
        await client.aclose()
