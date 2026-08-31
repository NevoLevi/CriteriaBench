from __future__ import annotations

from criteriabench.db.repositories import RunRepository
from criteriabench.db.session import Database
from criteriabench.domain.schemas import TrialDocument
from criteriabench.providers.base import ProviderResult, TokenUsage
from tests.helpers import criterion, extraction


async def test_repository_persists_run_lifecycle() -> None:
    database = Database("sqlite+aiosqlite:///:memory:")
    await database.initialize()
    repository = RunRepository(database)
    trial = TrialDocument(
        trial_id="TEST-001",
        title="Persistence test",
        eligibility_text="Age >= 18 years",
    )
    try:
        run = await repository.create_extraction(
            trial,
            provider="mock",
            model="deterministic-rules-v1",
        )
        await repository.mark_running(run.id)
        await repository.mark_completed(
            run.id,
            ProviderResult(
                extraction=extraction(criterion()),
                provider="mock",
                model="deterministic-rules-v1",
                latency_ms=1.25,
                usage=TokenUsage(),
                estimated_cost_usd=0.0,
            ),
        )
        stored = await repository.get_extraction(run.id)
        assert stored is not None
        assert stored.status == "completed"
        assert stored.result_json is not None
        assert stored.request_json["source_url"] is None
        assert stored.completed_at is not None
    finally:
        await database.close()
