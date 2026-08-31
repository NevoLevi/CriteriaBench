"""Extraction orchestration, provenance validation, persistence, and cost guards."""

from __future__ import annotations

import asyncio
import json
import math
from dataclasses import dataclass

from criteriabench.db.repositories import RunRepository
from criteriabench.domain.schemas import ClinicalTrialEligibility, TrialDocument
from criteriabench.evaluation.cost import calculate_token_cost
from criteriabench.observability import EXTRACTIONS, record_provider_result
from criteriabench.providers.base import ExtractionProvider, ProviderError, ProviderResult
from criteriabench.providers.openai import _INSTRUCTIONS

# Counting each UTF-8 byte as a token is deliberately conservative. The wrapper
# margin covers Responses API framing not represented by the visible strings.
_OPENAI_FIXED_INPUT_BYTES = (
    len(_INSTRUCTIONS.encode("utf-8"))
    + len(
        json.dumps(
            ClinicalTrialEligibility.model_json_schema(),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    )
    + 2_000
)


class BudgetExceeded(ProviderError):
    """A paid call was prevented by the configured authorization guard."""


class ProvenanceError(ProviderError):
    """Provider output did not point to exact input evidence."""


class LiveBudget:
    """Process-local conservative authorization ledger for an acknowledged CLI run."""

    def __init__(self, maximum_usd: float) -> None:
        if not math.isfinite(maximum_usd) or maximum_usd < 0:
            raise ValueError("maximum budget must be finite and non-negative")
        self.maximum_usd = maximum_usd
        self.spent_usd = 0.0
        self.reserved_usd = 0.0
        self._lock = asyncio.Lock()

    @property
    def remaining_usd(self) -> float:
        return max(0.0, self.maximum_usd - self.spent_usd - self.reserved_usd)

    async def reserve(self, estimate_usd: float) -> None:
        _validate_cost(estimate_usd, label="cost estimate")
        async with self._lock:
            if self.spent_usd + self.reserved_usd + estimate_usd > self.maximum_usd:
                raise BudgetExceeded("the configured live-run budget would be exceeded")
            self.reserved_usd += estimate_usd

    async def reconcile(self, estimate_usd: float, actual_usd: float) -> None:
        """Consume at least the reservation, covering unreported retries conservatively."""

        _validate_cost(estimate_usd, label="cost estimate")
        _validate_cost(actual_usd, label="actual cost")
        async with self._lock:
            self.reserved_usd = max(0.0, self.reserved_usd - estimate_usd)
            self.spent_usd += max(estimate_usd, actual_usd)

    async def release(self, estimate_usd: float) -> None:
        """Release only when no provider request could have started."""

        _validate_cost(estimate_usd, label="cost estimate")
        async with self._lock:
            self.reserved_usd = max(0.0, self.reserved_usd - estimate_usd)


@dataclass(frozen=True, slots=True)
class ExtractionOutcome:
    run_id: str | None
    status: str
    result: ProviderResult


class ExtractionService:
    def __init__(
        self,
        *,
        provider: ExtractionProvider,
        repository: RunRepository,
        live_budget: LiveBudget,
        estimated_input_tokens: int,
        max_output_tokens: int,
        input_price: float,
        output_price: float,
        max_document_characters: int,
        max_attempts: int = 1,
    ) -> None:
        if max_attempts < 1:
            raise ValueError("max_attempts must be at least one")
        self.provider = provider
        self.repository = repository
        self.live_budget = live_budget
        self.estimated_input_tokens = estimated_input_tokens
        self.max_output_tokens = max_output_tokens
        self.input_price = input_price
        self.output_price = output_price
        self.max_document_characters = max_document_characters
        self.max_attempts = max_attempts

    def estimated_request_cost(self, trial: TrialDocument) -> float:
        if self.provider.name == "mock":
            return 0.0
        serialized_bytes = len(trial.model_dump_json().encode("utf-8"))
        conservative_input_tokens = max(
            self.estimated_input_tokens,
            serialized_bytes + _OPENAI_FIXED_INPUT_BYTES,
        )
        per_attempt = calculate_token_cost(
            conservative_input_tokens,
            self.max_output_tokens,
            input_per_million_usd=self.input_price,
            output_per_million_usd=self.output_price,
        )
        return round(per_attempt * self.max_attempts, 6)

    async def execute(
        self,
        trial: TrialDocument,
        *,
        persist: bool = True,
        existing_run_id: str | None = None,
    ) -> ExtractionOutcome:
        if len(trial.eligibility_text) > self.max_document_characters:
            raise ValueError("eligibility text exceeds the configured character limit")

        estimate = self.estimated_request_cost(trial)
        await self.live_budget.reserve(estimate)
        run_id = existing_run_id
        provider_call_started = False
        authorization_settled = False
        try:
            if persist and run_id is None:
                run = await self.repository.create_extraction(
                    trial,
                    provider=self.provider.name,
                    model=self.provider.model,
                    status="queued",
                )
                run_id = run.id
            if run_id is not None:
                await self.repository.mark_running(run_id)

            provider_call_started = True
            result = await self.provider.extract(trial)
            _validate_provenance(trial, result)
            await self.live_budget.reconcile(estimate, result.estimated_cost_usd)
            authorization_settled = True
            record_provider_result(result)
            if run_id is not None:
                await self.repository.mark_completed(run_id, result)
            return ExtractionOutcome(run_id=run_id, status="completed", result=result)
        except Exception:
            if not authorization_settled:
                if provider_call_started:
                    await self.live_budget.reconcile(estimate, estimate)
                else:
                    await self.live_budget.release(estimate)
            EXTRACTIONS.labels(self.provider.name, "failed").inc()
            if run_id is not None:
                await self.repository.mark_failed(run_id, error_code="extraction_failed")
            raise


def _validate_cost(value: float, *, label: str) -> None:
    if not math.isfinite(value) or value < 0:
        raise ValueError(f"{label} must be finite and non-negative")


def _validate_provenance(trial: TrialDocument, result: ProviderResult) -> None:
    extraction = result.extraction
    if extraction.trial_id != trial.trial_id:
        raise ProvenanceError("provider output trial_id does not match the request")
    for criterion in extraction.inclusion_criteria + extraction.exclusion_criteria:
        evidence = criterion.evidence
        if evidence.end_char > len(trial.eligibility_text):
            raise ProvenanceError("provider evidence offsets exceed the source document")
        observed = trial.eligibility_text[evidence.start_char : evidence.end_char]
        if observed != evidence.quote:
            raise ProvenanceError("provider evidence quote does not match its character offsets")
        if evidence.quote != criterion.source_text:
            raise ProvenanceError("criterion source_text must exactly match its evidence quote")
