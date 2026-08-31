"""Provider-independent extraction contracts."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

from criteriabench.domain.schemas import ClinicalTrialEligibility, TrialDocument


class ProviderError(RuntimeError):
    """Safe provider failure suitable for translation at the API boundary."""


@dataclass(frozen=True, slots=True)
class TokenUsage:
    input_tokens: int = 0
    output_tokens: int = 0

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens


@dataclass(frozen=True, slots=True)
class ProviderResult:
    extraction: ClinicalTrialEligibility
    provider: str
    model: str
    latency_ms: float
    usage: TokenUsage
    estimated_cost_usd: float
    response_id: str | None = None


class ExtractionProvider(ABC):
    """Interface implemented by paid and deterministic providers."""

    name: str
    model: str

    @abstractmethod
    async def extract(self, trial: TrialDocument) -> ProviderResult:
        """Extract a strictly validated eligibility structure."""
