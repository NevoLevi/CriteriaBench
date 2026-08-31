from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient

from criteriabench.api.app import create_app
from criteriabench.config import Settings


class FakeQueue:
    async def ping(self) -> bool:
        return True

    async def enqueue(self, job: Any) -> None:
        del job

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


def _extraction_payload() -> dict[str, object]:
    return {
        "trial": {
            "trial_id": "TEST-001",
            "title": "Integrity test",
            "eligibility_text": "Inclusion Criteria:\n- Age >= 18 years",
            "source_url": None,
        },
        "persist": True,
        "execution_mode": "sync",
    }


def test_evaluation_link_must_exist_and_belong_to_the_same_trial() -> None:
    app = create_app(_settings(), queue=FakeQueue())
    with TestClient(app) as client:
        extraction_response = client.post(
            "/api/v1/extractions",
            json=_extraction_payload(),
        )
        assert extraction_response.status_code == 200
        extraction_payload = extraction_response.json()
        structured = extraction_payload["result"]

        missing = client.post(
            "/api/v1/evaluations",
            json={
                "prediction": structured,
                "reference": structured,
                "persist": True,
                "extraction_run_id": "00000000-0000-0000-0000-000000000000",
            },
        )
        assert missing.status_code == 422

        wrong_trial = dict(structured)
        wrong_trial["trial_id"] = "OTHER-TRIAL"
        mismatched = client.post(
            "/api/v1/evaluations",
            json={
                "prediction": wrong_trial,
                "reference": wrong_trial,
                "persist": True,
                "extraction_run_id": extraction_payload["run_id"],
            },
        )
        assert mismatched.status_code == 422

        linked = client.post(
            "/api/v1/evaluations",
            json={
                "prediction": structured,
                "reference": structured,
                "persist": True,
                "extraction_run_id": extraction_payload["run_id"],
            },
        )
        assert linked.status_code == 200
        assert linked.json()["evaluation_id"] is not None


def test_unmatched_paths_use_a_bounded_metrics_label() -> None:
    random_path = "/does-not-exist/private-cardinality-value-48391"
    app = create_app(_settings(), queue=FakeQueue())
    with TestClient(app) as client:
        assert client.get(random_path).status_code == 404
        metrics = client.get("/metrics").text
    assert random_path not in metrics
    assert 'route="__unmatched__"' in metrics


def test_openapi_describes_async_acceptance_response() -> None:
    app = create_app(_settings(), queue=FakeQueue())
    with TestClient(app) as client:
        operation = client.get("/openapi.json").json()["paths"]["/api/v1/extractions"]["post"]
    assert "202" in operation["responses"]
