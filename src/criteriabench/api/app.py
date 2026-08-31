"""FastAPI application factory and operational endpoints."""

from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from time import perf_counter
from uuid import uuid4

from fastapi import FastAPI, Request, Response
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

from criteriabench.api.routes import build_api_router
from criteriabench.api.schemas import ReadinessResponse
from criteriabench.config import Settings, get_settings
from criteriabench.db.repositories import RunRepository
from criteriabench.db.session import Database
from criteriabench.observability import HTTP_DURATION, HTTP_REQUESTS
from criteriabench.providers.base import ExtractionProvider
from criteriabench.providers.factory import create_provider
from criteriabench.queue import RedisQueue
from criteriabench.services.extraction import ExtractionService, LiveBudget


def create_app(
    settings: Settings | None = None,
    *,
    provider: ExtractionProvider | None = None,
    database: Database | None = None,
    queue: RedisQueue | None = None,
) -> FastAPI:
    """Build an isolated application instance for production or tests."""

    resolved_settings = settings or get_settings()
    resolved_database = database or Database(resolved_settings.database_url)
    resolved_provider = provider or create_provider(resolved_settings)
    resolved_queue = queue or RedisQueue(
        resolved_settings.redis_url,
        resolved_settings.queue_name,
    )
    repository = RunRepository(resolved_database)
    extraction_service = ExtractionService(
        provider=resolved_provider,
        repository=repository,
        live_budget=LiveBudget(resolved_settings.live_run_budget_usd),
        estimated_input_tokens=resolved_settings.estimated_input_tokens_per_request,
        max_output_tokens=resolved_settings.max_output_tokens,
        input_price=resolved_settings.input_cost_per_million_usd,
        output_price=resolved_settings.output_cost_per_million_usd,
        max_document_characters=resolved_settings.max_document_characters,
        max_attempts=(
            resolved_settings.openai_max_retries + 1 if resolved_provider.name == "openai" else 1
        ),
    )

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        del app
        if resolved_settings.auto_create_schema:
            await resolved_database.initialize()
        yield
        await resolved_queue.close()
        await resolved_database.close()

    app = FastAPI(
        title=resolved_settings.app_name,
        version="0.1.0",
        description=(
            "Reproducible extraction and evaluation of public clinical-trial "
            "eligibility criteria. Not a clinical decision-support system."
        ),
        lifespan=lifespan,
        docs_url=None if resolved_settings.environment == "production" else "/docs",
        redoc_url=None,
    )
    app.state.settings = resolved_settings
    app.state.database = resolved_database
    app.state.provider = resolved_provider
    app.state.queue = resolved_queue
    app.state.repository = repository
    app.state.extraction_service = extraction_service

    @app.middleware("http")
    async def operational_middleware(
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        started = perf_counter()
        request_id = request.headers.get("x-request-id") or str(uuid4())
        try:
            response = await call_next(request)
        except Exception:
            route = _route_template(request)
            HTTP_REQUESTS.labels(request.method, route, "500").inc()
            HTTP_DURATION.labels(request.method, route).observe(perf_counter() - started)
            raise
        route = _route_template(request)
        HTTP_REQUESTS.labels(request.method, route, str(response.status_code)).inc()
        HTTP_DURATION.labels(request.method, route).observe(perf_counter() - started)
        response.headers["x-request-id"] = request_id
        response.headers["x-content-type-options"] = "nosniff"
        response.headers["x-frame-options"] = "DENY"
        response.headers["referrer-policy"] = "no-referrer"
        return response

    @app.get("/healthz", include_in_schema=False)
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/readyz", response_model=ReadinessResponse, include_in_schema=False)
    async def readiness(response: Response) -> ReadinessResponse:
        database_ok = await resolved_database.ping()
        redis_state = "not_required"
        redis_ok = True
        if resolved_settings.readiness_requires_redis:
            redis_ok = await resolved_queue.ping()
            redis_state = "up" if redis_ok else "down"
        ready = database_ok and redis_ok
        if not ready:
            response.status_code = 503
        return ReadinessResponse(
            status="ready" if ready else "not_ready",
            database="up" if database_ok else "down",
            redis=redis_state,
        )

    @app.get("/metrics", include_in_schema=False)
    async def metrics() -> Response:
        return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)

    app.include_router(build_api_router(), prefix=resolved_settings.api_prefix)
    return app


def _route_template(request: Request) -> str:
    """Return only bounded route templates for Prometheus labels."""

    route = request.scope.get("route")
    template = getattr(route, "path", None)
    return template if isinstance(template, str) else "__unmatched__"
