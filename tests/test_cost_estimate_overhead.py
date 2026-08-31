from __future__ import annotations

from criteriabench.db.repositories import RunRepository
from criteriabench.db.session import Database
from criteriabench.domain.schemas import TrialDocument
from criteriabench.services.extraction import (
    _OPENAI_FIXED_INPUT_BYTES,
    ExtractionService,
    LiveBudget,
)
from tests.test_service import CountingPaidProvider


async def test_paid_estimate_includes_prompt_schema_and_wrapper_overhead() -> None:
    database = Database("sqlite+aiosqlite:///:memory:")
    trial = TrialDocument(trial_id="T", title="T", eligibility_text="Adult")
    service = ExtractionService(
        provider=CountingPaidProvider(),
        repository=RunRepository(database),
        live_budget=LiveBudget(2.0),
        estimated_input_tokens=1,
        max_output_tokens=1,
        input_price=1.0,
        output_price=1.0,
        max_document_characters=100_000,
    )
    try:
        assert _OPENAI_FIXED_INPUT_BYTES > 5_392
        old_fixed_overhead_cost = (
            len(trial.model_dump_json().encode("utf-8")) + 4_000 + 1
        ) / 1_000_000
        assert service.estimated_request_cost(trial) > old_fixed_overhead_cost
    finally:
        await database.close()
