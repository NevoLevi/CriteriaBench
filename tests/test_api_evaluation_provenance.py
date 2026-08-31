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


def _extraction_payload(*, execution_mode: str) -> dict[str, object]:
    return {
        "trial": {
            "trial_id": "TEST-PROVENANCE-001",
            "title": "Evaluation provenance test",
            "eligibility_text": "Inclusion Criteria:\n- Age >= 18 years",
            "source_url": None,
        },
        "persist": True,
        "execution_mode": execution_mode,
    }


def _evaluation_payload(
    prediction: dict[str, object],
    *,
    run_id: str,
) -> dict[str, object]:
    return {
        "prediction": prediction,
        "reference": prediction,
        "persist": True,
        "extraction_run_id": run_id,
    }


def test_linked_evaluation_requires_completed_exact_stored_prediction() -> None:
    app = create_app(_settings(), queue=FakeQueue())
    with TestClient(app) as client:
        completed_response = client.post(
            "/api/v1/extractions",
            json=_extraction_payload(execution_mode="sync"),
        )
        assert completed_response.status_code == 200
        completed = completed_response.json()
        prediction = completed["result"]

        queued_response = client.post(
            "/api/v1/extractions",
            json=_extraction_payload(execution_mode="async"),
        )
        assert queued_response.status_code == 202
        queued = queued_response.json()
        incomplete = client.post(
            "/api/v1/evaluations",
            json=_evaluation_payload(prediction, run_id=queued["run_id"]),
        )
        assert incomplete.status_code == 422
        assert incomplete.json()["detail"] == "extraction_run_id is not completed"

        fabricated = dict(prediction)
        fabricated["ambiguities"] = [*prediction["ambiguities"], "fabricated"]
        mismatched = client.post(
            "/api/v1/evaluations",
            json=_evaluation_payload(fabricated, run_id=completed["run_id"]),
        )
        assert mismatched.status_code == 422
        assert (
            mismatched.json()["detail"] == "prediction does not match the linked extraction result"
        )

        linked = client.post(
            "/api/v1/evaluations",
            json=_evaluation_payload(prediction, run_id=completed["run_id"]),
        )
        assert linked.status_code == 200
        assert linked.json()["evaluation_id"] is not None
