"""OpenAI Responses API adapter with strict structured output validation."""

from __future__ import annotations

import json
from time import perf_counter
from typing import Any

from openai import AsyncOpenAI

from criteriabench.domain.schemas import ClinicalTrialEligibility, TrialDocument
from criteriabench.evaluation.cost import calculate_token_cost
from criteriabench.providers.base import (
    ExtractionProvider,
    ProviderError,
    ProviderResult,
    TokenUsage,
)

OPENAI_API_BASE_URL = "https://api.openai.com/v1"

_INSTRUCTIONS = """You extract clinical-trial eligibility criteria for benchmarking.
Return only data matching the supplied JSON schema. Split compound bullets into independently
evaluable criteria and connect them through the same AND/OR logic group. Preserve exact evidence
quotes and character offsets into eligibility_text. Offsets are zero-based Unicode code-point
indexes into eligibility_text, and end_char is exclusive. Before returning, self-check every
criterion so eligibility_text[evidence.start_char:evidence.end_char] exactly equals evidence.quote
and evidence.quote exactly equals source_text. Represent negation and temporal constraints
explicitly. Never infer facts that are not stated. Put genuine uncertainty in ambiguities.
Criterion IDs are sequential I001... for inclusion and E001... for exclusion.
"""


class OpenAIResponsesProvider(ExtractionProvider):
    """Paid provider that remains opt-in through explicit application configuration."""

    name = "openai"

    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        timeout_seconds: float,
        max_retries: int,
        max_output_tokens: int,
        input_cost_per_million_usd: float,
        output_cost_per_million_usd: float,
        client: Any | None = None,
    ) -> None:
        if not api_key:
            raise ValueError("an API key is required for the OpenAI provider")
        self.model = model
        self._max_output_tokens = max_output_tokens
        self._input_price = input_cost_per_million_usd
        self._output_price = output_cost_per_million_usd
        self._client = client or AsyncOpenAI(
            api_key=api_key,
            base_url=OPENAI_API_BASE_URL,
            timeout=timeout_seconds,
            max_retries=max_retries,
        )

    async def extract(self, trial: TrialDocument) -> ProviderResult:
        payload = json.dumps(trial.model_dump(mode="json"), ensure_ascii=False)
        started = perf_counter()
        try:
            response = await self._client.responses.create(
                model=self.model,
                instructions=_INSTRUCTIONS,
                input=payload,
                reasoning={"effort": "none"},
                text={
                    "format": {
                        "type": "json_schema",
                        "name": "clinical_trial_eligibility",
                        "schema": ClinicalTrialEligibility.model_json_schema(),
                        "strict": True,
                    }
                },
                max_output_tokens=self._max_output_tokens,
                store=False,
            )
            output_text = response.output_text
            if not output_text:
                raise ProviderError("the model returned no structured output")
            extraction = ClinicalTrialEligibility.model_validate_json(output_text)
        except ProviderError:
            raise
        except Exception as exc:
            # Never reflect SDK details: they can contain request metadata.
            raise ProviderError("the configured extraction provider failed") from exc

        usage_object = getattr(response, "usage", None)
        usage = TokenUsage(
            input_tokens=int(getattr(usage_object, "input_tokens", 0) or 0),
            output_tokens=int(getattr(usage_object, "output_tokens", 0) or 0),
        )
        return ProviderResult(
            extraction=extraction,
            provider=self.name,
            model=self.model,
            latency_ms=round((perf_counter() - started) * 1_000, 3),
            usage=usage,
            estimated_cost_usd=calculate_token_cost(
                usage.input_tokens,
                usage.output_tokens,
                input_per_million_usd=self._input_price,
                output_per_million_usd=self._output_price,
            ),
            response_id=getattr(response, "id", None),
        )
