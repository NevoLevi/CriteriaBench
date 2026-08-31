from __future__ import annotations

import pytest

from criteriabench.db.repositories import RunRepository
from criteriabench.db.session import Database
from criteriabench.domain.schemas import TrialDocument
from criteriabench.providers.base import ExtractionProvider, ProviderResult, TokenUsage
from criteriabench.services.extraction import BudgetExceeded, ExtractionService, LiveBudget
from tests.helpers import criterion, extraction


class CountingPaidProvider(ExtractionProvider):
    name = "openai"
    model = "test-model"

    def __init__(self) -> None:
        self.calls = 0

    async def extract(self, trial: TrialDocument) -> ProviderResult:
        self.calls += 1
        item = criterion(text=trial.eligibility_text)
        return ProviderResult(
            extraction=extraction(item, trial_id=trial.trial_id),
            provider=self.name,
            model=self.model,
            latency_ms=1.0,
            usage=TokenUsage(input_tokens=100, output_tokens=50),
            estimated_cost_usd=0.00015,
        )


async def test_budget_is_reserved_before_a_run_or_paid_call_is_created() -> None:
    database = Database("sqlite+aiosqlite:///:memory:")
    await database.initialize()
    repository = RunRepository(database)
    provider = CountingPaidProvider()
    trial = TrialDocument(
        trial_id="TEST-001",
        title="Budget test",
        eligibility_text="Age >= 18 years",
    )
    service = ExtractionService(
        provider=provider,
        repository=repository,
        live_budget=LiveBudget(0.00001),
        estimated_input_tokens=1_000,
        max_output_tokens=1_000,
        input_price=1.0,
        output_price=1.0,
        max_document_characters=100_000,
    )
    try:
        with pytest.raises(BudgetExceeded):
            await service.execute(trial, persist=True)
        assert provider.calls == 0
        assert await repository.list_extractions() == []
    finally:
        await database.close()


async def test_input_estimate_increases_with_actual_document_size() -> None:
    database = Database("sqlite+aiosqlite:///:memory:")
    provider = CountingPaidProvider()
    service = ExtractionService(
        provider=provider,
        repository=RunRepository(database),
        live_budget=LiveBudget(2.0),
        estimated_input_tokens=100,
        max_output_tokens=100,
        input_price=1.0,
        output_price=1.0,
        max_document_characters=100_000,
    )
    short = TrialDocument(trial_id="S", title="S", eligibility_text="Adult")
    long = TrialDocument(trial_id="L", title="L", eligibility_text="Adult " * 5_000)
    try:
        assert service.estimated_request_cost(long) > service.estimated_request_cost(short)
    finally:
        await database.close()
