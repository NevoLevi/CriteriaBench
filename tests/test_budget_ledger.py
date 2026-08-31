from __future__ import annotations

import pytest

from criteriabench.db.repositories import RunRepository
from criteriabench.db.session import Database
from criteriabench.domain.schemas import TrialDocument
from criteriabench.providers.base import ExtractionProvider, ProviderError, ProviderResult
from criteriabench.services.extraction import ExtractionService, LiveBudget


async def test_success_consumes_conservative_reservation_not_only_reported_cost() -> None:
    budget = LiveBudget(1.0)
    await budget.reserve(0.6)
    await budget.reconcile(0.6, 0.1)
    assert budget.spent_usd == pytest.approx(0.6)
    assert budget.remaining_usd == pytest.approx(0.4)


class FailingPaidProvider(ExtractionProvider):
    name = "openai"
    model = "test-model"

    async def extract(self, trial: TrialDocument) -> ProviderResult:
        del trial
        raise ProviderError("safe simulated failure")


async def test_started_failed_call_keeps_reservation_consumed() -> None:
    database = Database("sqlite+aiosqlite:///:memory:")
    budget = LiveBudget(1.0)
    service = ExtractionService(
        provider=FailingPaidProvider(),
        repository=RunRepository(database),
        live_budget=budget,
        estimated_input_tokens=1_000,
        max_output_tokens=1_000,
        input_price=1.0,
        output_price=1.0,
        max_document_characters=100_000,
        max_attempts=2,
    )
    trial = TrialDocument(trial_id="T", title="T", eligibility_text="Adult")
    estimate = service.estimated_request_cost(trial)
    try:
        with pytest.raises(ProviderError):
            await service.execute(trial, persist=False)
        assert budget.spent_usd == pytest.approx(estimate)
        assert budget.reserved_usd == 0.0
    finally:
        await database.close()
