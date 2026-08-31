from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient

from criteriabench.api.app import create_app
from criteriabench.config import Settings


class FakeQueue:
    def __init__(self) -> None:
        self.jobs: list[Any] = []

    async def ping(self) -> bool:
        return True

    async def enqueue(self, job: Any) -> None:
        self.jobs.append(job)

    async def close(self) -> None:
        return None


def _settings() -> Settings:
    return Settings(
        _env_file=None,
        DATABASE_URL="sqlite+aiosqlite:///:memory:",
        REDIS_URL="redis://unused",
        LLM_PROVIDER="mock",
        ALLOW_PAID_CALLS=False,
        CRITERIABENCH_ENVIRONMENT="test",
    )


def _request(mode: str = "sync") -> dict[str, object]:
    return {
        "trial": {
            "trial_id": "TEST-001",
            "title": "API test",
            "eligibility_text": (
                "Inclusion Criteria:\n- Age >= 18 years\n\nExclusion Criteria:\n- Pregnancy"
            ),
            "source_url": None,
        },
        "persist": True,
        "execution_mode": mode,
    }


def test_operational_endpoints_and_sync_extraction() -> None:
    app = create_app(_settings(), queue=FakeQueue())
    with TestClient(app) as client:
        assert client.get("/healthz").json() == {"status": "ok"}
        ready = client.get("/readyz")
        assert ready.status_code == 200
        assert ready.json()["database"] == "up"
        response = client.post("/api/v1/extractions", json=_request())
        assert response.status_code == 200
        payload = response.json()
        assert payload["status"] == "completed"
        assert payload["provider"] == "mock"
        assert payload["estimated_cost_usd"] == 0.0
        assert payload["result"]["schema_version"] == "1.0"
        assert "criteriabench_http_requests_total" in client.get("/metrics").text


def test_mock_async_extraction_is_enqueued() -> None:
    queue = FakeQueue()
    app = create_app(_settings(), queue=queue)
    with TestClient(app) as client:
        response = client.post("/api/v1/extractions", json=_request("async"))
        assert response.status_code == 202
        assert response.json()["status"] == "queued"
        assert len(queue.jobs) == 1


def test_validation_rejects_unknown_request_fields() -> None:
    app = create_app(_settings(), queue=FakeQueue())
    payload = _request()
    payload["unexpected"] = True
    with TestClient(app) as client:
        assert client.post("/api/v1/extractions", json=payload).status_code == 422
