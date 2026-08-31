"""Redis list queue with atomic claim, explicit acknowledgement, and recovery."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal
from uuid import UUID, uuid4

from pydantic import Field, ValidationError
from redis.asyncio import Redis
from redis.exceptions import RedisError

from criteriabench.domain.schemas import StrictModel, TrialDocument

DEAD_LETTER_MAX_ITEMS = 100


class QueueUnavailable(RuntimeError):
    """Redis could not perform a required queue operation."""


class ExtractionJob(StrictModel):
    """Immutable work contract captured by the API at enqueue time."""

    run_id: str = Field(min_length=1, max_length=100)
    trial: TrialDocument
    provider: Literal["mock", "openai"]
    model: str = Field(min_length=1, max_length=200)
    schema_version: Literal["1.0"]
    contract_hash: str = Field(pattern=r"^[0-9a-f]{64}$")


class _QueueEnvelope(StrictModel):
    """Unique queue identity prevents value-based ACK ambiguity."""

    message_id: UUID
    job: ExtractionJob


@dataclass(frozen=True, slots=True)
class QueuedMessage:
    """A claimed message that remains recoverable until acknowledged."""

    job: ExtractionJob
    raw_payload: str


class RedisQueue:
    """Single-worker, at-least-once Redis list queue.

    LPUSH + BRPOPLPUSH preserves FIFO order and atomically moves a claimed item
    to a processing list. The worker ACKs only after terminal persistence. A
    single replacement worker recovers unacknowledged items on startup.
    """

    def __init__(self, url: str, queue_name: str, *, client: Any | None = None) -> None:
        self.queue_name = queue_name
        self.processing_name = f"{queue_name}:processing"
        self.dead_letter_name = f"{queue_name}:dead-letter"
        self._client: Any = client or Redis.from_url(url, decode_responses=True)

    async def ping(self) -> bool:
        try:
            result = await self._client.ping()
        except RedisError:
            return False
        return bool(result)

    async def enqueue(self, job: ExtractionJob) -> None:
        payload = _QueueEnvelope(message_id=uuid4(), job=job).model_dump_json()
        try:
            await self._client.lpush(self.queue_name, payload)
        except RedisError as exc:
            raise QueueUnavailable("the work queue is unavailable") from exc

    async def dequeue(self, timeout_seconds: int) -> QueuedMessage | None:
        try:
            payload = await self._client.brpoplpush(
                self.queue_name,
                self.processing_name,
                timeout=timeout_seconds,
            )
        except RedisError as exc:
            raise QueueUnavailable("the work queue is unavailable") from exc
        if payload is None:
            return None

        raw_payload = str(payload)
        try:
            envelope = _QueueEnvelope.model_validate_json(raw_payload)
        except ValidationError:
            await self._dead_letter(raw_payload)
            return None
        return QueuedMessage(job=envelope.job, raw_payload=raw_payload)

    async def acknowledge(self, message: QueuedMessage) -> None:
        try:
            removed = await self._client.lrem(
                self.processing_name,
                1,
                message.raw_payload,
            )
        except RedisError as exc:
            raise QueueUnavailable("could not acknowledge the processed job") from exc
        if int(removed) != 1:
            raise QueueUnavailable("processed job was not present for acknowledgement")

    async def recover_processing(self) -> int:
        """Requeue unacknowledged items before starting the single worker."""

        recovered = 0
        try:
            while True:
                payload = await self._client.rpoplpush(
                    self.processing_name,
                    self.queue_name,
                )
                if payload is None:
                    return recovered
                recovered += 1
        except RedisError as exc:
            raise QueueUnavailable("could not recover interrupted jobs") from exc

    async def close(self) -> None:
        await self._client.aclose()

    async def _dead_letter(self, raw_payload: str) -> None:
        """Atomically remove poison and retain only a bounded diagnostic tail."""

        try:
            if hasattr(self._client, "eval"):
                removed = await self._client.eval(
                    """
                    local removed = redis.call('LREM', KEYS[1], 1, ARGV[1])
                    if removed == 1 then
                      redis.call('LPUSH', KEYS[2], ARGV[1])
                      redis.call('LTRIM', KEYS[2], 0, tonumber(ARGV[2]) - 1)
                    end
                    return removed
                    """,
                    2,
                    self.processing_name,
                    self.dead_letter_name,
                    raw_payload,
                    DEAD_LETTER_MAX_ITEMS,
                )
            else:
                # Tiny unit-test fakes do not implement Lua. Production Redis
                # always uses the atomic branch above.
                removed = await self._client.lrem(self.processing_name, 1, raw_payload)
                if int(removed) == 1:
                    await self._client.lpush(self.dead_letter_name, raw_payload)
                    if hasattr(self._client, "ltrim"):
                        await self._client.ltrim(
                            self.dead_letter_name,
                            0,
                            DEAD_LETTER_MAX_ITEMS - 1,
                        )
            if int(removed) != 1:
                raise QueueUnavailable("malformed job was not present in processing")
        except RedisError as exc:
            raise QueueUnavailable("could not dead-letter a malformed job") from exc
