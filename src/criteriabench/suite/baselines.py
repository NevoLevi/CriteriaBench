"""Allowlisted, zero-network baselines for the offline suite."""

from __future__ import annotations

from typing import Protocol

from criteriabench.domain.schemas import ClinicalTrialEligibility, TrialDocument
from criteriabench.providers.mock import DeterministicMockProvider
from criteriabench.suite.models import BaselineName

ALLOWED_BASELINES: tuple[BaselineName, ...] = ("empty-v1", "rules-v1")


class OfflineBaseline(Protocol):
    name: BaselineName

    async def predict(self, trial: TrialDocument) -> ClinicalTrialEligibility:
        """Return one typed prediction without external I/O."""


class EmptyBaseline:
    name: BaselineName = "empty-v1"

    async def predict(self, trial: TrialDocument) -> ClinicalTrialEligibility:
        return ClinicalTrialEligibility(
            schema_version="1.0",
            trial_id=trial.trial_id,
            inclusion_criteria=[],
            exclusion_criteria=[],
            ambiguities=["Offline empty baseline intentionally emits no criteria."],
        )


class RulesBaseline:
    """Thin adapter over the project's deterministic mock extraction provider."""

    name: BaselineName = "rules-v1"

    def __init__(self) -> None:
        self._provider = DeterministicMockProvider()

    async def predict(self, trial: TrialDocument) -> ClinicalTrialEligibility:
        result = await self._provider.extract(trial)
        return result.extraction


def create_baseline(name: BaselineName) -> OfflineBaseline:
    if name == "empty-v1":
        return EmptyBaseline()
    if name == "rules-v1":
        return RulesBaseline()
    raise ValueError(f"unsupported offline baseline: {name}")
