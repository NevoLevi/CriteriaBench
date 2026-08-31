"""Single-worker queue consumer with recoverable claims and safe logging."""

from __future__ import annotations

import asyncio
import logging
import signal
from typing import Any

from prometheus_client import start_http_server

from criteriabench.config import Settings, get_settings
from criteriabench.db.repositories import RunRepository
from criteriabench.db.session import Database
from criteriabench.providers.factory import create_provider
from criteriabench.queue import QueueUnavailable, RedisQueue
from criteriabench.services.extraction import ExtractionService, LiveBudget
from criteriabench.worker.processor import process_message

LOGGER = logging.getLogger("criteriabench.worker")


async def run_worker(settings: Settings | None = None) -> None:
    resolved = settings or get_settings()
    if resolved.provider != "mock" or resolved.allow_paid_calls:
        raise RuntimeError(
            "the queue worker is mock-only; paid extraction is restricted to the benchmark CLI"
        )

    logging.basicConfig(level=resolved.log_level)
    database = Database(resolved.database_url)
    repository = RunRepository(database)
    provider = create_provider(resolved)
    queue = RedisQueue(resolved.redis_url, resolved.queue_name)
    service = ExtractionService(
        provider=provider,
        repository=repository,
        live_budget=LiveBudget(0.0),
        estimated_input_tokens=resolved.estimated_input_tokens_per_request,
        max_output_tokens=resolved.max_output_tokens,
        input_price=resolved.input_cost_per_million_usd,
        output_price=resolved.output_cost_per_million_usd,
        max_document_characters=resolved.max_document_characters,
        max_attempts=1,
    )
    stop = asyncio.Event()
    _install_signal_handlers(stop)
    metrics_server: Any = None
    metrics_thread: Any = None
    try:
        if resolved.auto_create_schema:
            await database.initialize()
        recovered = await queue.recover_processing()
        metrics_server, metrics_thread = start_http_server(resolved.worker_metrics_port)
        LOGGER.info(
            "worker_started provider=%s model=%s recovered=%s",
            provider.name,
            provider.model,
            recovered,
        )
        while not stop.is_set():
            try:
                message = await queue.dequeue(resolved.worker_poll_seconds)
            except QueueUnavailable:
                LOGGER.warning("queue_temporarily_unavailable")
                await asyncio.sleep(1)
                continue
            if message is None:
                continue
            try:
                outcome = await process_message(
                    message,
                    service=service,
                    repository=repository,
                    queue=queue,
                )
                if outcome == "retry_pending":
                    LOGGER.warning(
                        "job_interrupted run_id=%s recovery=worker_restart",
                        message.job.run_id,
                    )
                else:
                    LOGGER.info(
                        "job_terminal run_id=%s outcome=%s",
                        message.job.run_id,
                        outcome,
                    )
            except Exception as exc:
                # Never emit exception text or a traceback: SDK/DB errors can
                # include connection or request metadata.
                LOGGER.error(
                    "job_failed run_id=%s error_type=%s",
                    message.job.run_id,
                    type(exc).__name__,
                )
    finally:
        await queue.close()
        await database.close()
        if metrics_server is not None:
            await asyncio.to_thread(metrics_server.shutdown)
        if metrics_thread is not None:
            await asyncio.to_thread(metrics_thread.join, 1.0)
        LOGGER.info("worker_stopped")


def _install_signal_handlers(stop: asyncio.Event) -> None:
    loop = asyncio.get_running_loop()

    def request_stop(*_: object) -> None:
        loop.call_soon_threadsafe(stop.set)

    for signal_value in (signal.SIGINT, signal.SIGTERM):
        try:
            signal.signal(signal_value, request_stop)
        except (OSError, ValueError):
            continue
