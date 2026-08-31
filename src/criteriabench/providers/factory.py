"""Construct provider adapters from explicit settings."""

from __future__ import annotations

from criteriabench.config import Settings
from criteriabench.providers.base import ExtractionProvider
from criteriabench.providers.mock import DeterministicMockProvider
from criteriabench.providers.openai import OpenAIResponsesProvider


def create_provider(settings: Settings) -> ExtractionProvider:
    """Create a provider, failing closed unless paid use is explicitly enabled."""

    if settings.provider == "mock":
        return DeterministicMockProvider()
    if settings.provider == "openai":
        if not settings.allow_paid_calls:
            raise RuntimeError("OpenAI provider selected but ALLOW_PAID_CALLS is not enabled")
        if not settings.key_is_configured or settings.openai_api_key is None:
            raise RuntimeError("OpenAI provider selected but OPENAI_API_KEY is not configured")
        return OpenAIResponsesProvider(
            api_key=settings.openai_api_key.get_secret_value(),
            model=settings.openai_model,
            timeout_seconds=settings.openai_timeout_seconds,
            max_retries=settings.openai_max_retries,
            max_output_tokens=settings.max_output_tokens,
            input_cost_per_million_usd=settings.input_cost_per_million_usd,
            output_cost_per_million_usd=settings.output_cost_per_million_usd,
        )
    raise ValueError(f"unsupported provider: {settings.provider}")
