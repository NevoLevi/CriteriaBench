from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient

from criteriabench.api.app import create_app
from criteriabench.config import Settings
from criteriabench.domain.schemas import TrialDocument
from criteriabench.providers.base import ExtractionProvider, ProviderResult
from tests.helpers import criterion, extraction


class FakeQueue:
    async def ping(self) -> bool:
        return True

    async def enqueue(self, job: Any) -> None:
        raise AssertionError(f"paid job must not be enqueued: {type(job).__name__}")

    async def close(self) -> None:
        return None


class NeverCalledPaidProvider(ExtractionProvider):
    name = "openai"
    model = "test-live-model"

    async def extract(self, trial: TrialDocument) -> ProviderResult:
        raise AssertionError(f"unexpected paid call for {trial.trial_id}")


def _base_settings(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "DATABASE_URL": "sqlite+aiosqlite:///:memory:",
        "REDIS_URL": "redis://unused",
        "LLM_PROVIDER": "mock",
        "ALLOW_PAID_CALLS": False,
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)


def test_info_exposes_rates_and_boolean_gates_but_no_secret() -> None:
    app = create_app(_base_settings(), queue=FakeQueue())
    with TestClient(app) as client:
        payload = client.get("/api/v1/info").json()
    assert payload["paid_calls_enabled"] is False
    assert payload["authorization_guard_usd"] == 2.0
    assert payload["input_cost_per_million_usd"] == 0.2
    assert "api_key" not in str(payload).casefold()


def test_evaluation_endpoint_returns_field_level_report() -> None:
    prediction = extraction(criterion(category="demographic"))
    reference = extraction(criterion(category="age"))
    app = create_app(_base_settings(), queue=FakeQueue())
    with TestClient(app) as client:
        response = client.post(
            "/api/v1/evaluations",
            json={
                "prediction": prediction.model_dump(mode="json"),
                "reference": reference.model_dump(mode="json"),
                "persist": False,
                "extraction_run_id": None,
            },
        )
    assert response.status_code == 200
    assert response.json()["report"]["category_accuracy"] == 0.0
    assert response.json()["report"]["macro_field_accuracy"] < 1.0


def test_paid_provider_cannot_use_async_queue_path() -> None:
    settings = _base_settings(
        LLM_PROVIDER="openai",
        ALLOW_PAID_CALLS=True,
        OPENAI_API_KEY="unit-test-placeholder",
    )
    app = create_app(settings, provider=NeverCalledPaidProvider(), queue=FakeQueue())
    with TestClient(app) as client:
        response = client.post(
            "/api/v1/extractions",
            json={
                "trial": {
                    "trial_id": "TEST-001",
                    "title": "Paid async guard",
                    "eligibility_text": "Inclusion Criteria:\n- Adult",
                    "source_url": None,
                },
                "persist": True,
                "execution_mode": "async",
            },
        )
    assert response.status_code == 422
