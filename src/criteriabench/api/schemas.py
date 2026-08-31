"""Strict request and response schemas for the public API."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import Field, model_validator

from criteriabench.domain.schemas import (
    ClinicalTrialEligibility,
    StrictModel,
    TrialDocument,
)
from criteriabench.evaluation.metrics import EvaluationReport


class ExtractionExecutionRequest(StrictModel):
    trial: TrialDocument
    persist: bool = True
    execution_mode: Literal["sync", "async"] = "sync"

    @model_validator(mode="after")
    def async_runs_are_persisted(self) -> ExtractionExecutionRequest:
        if self.execution_mode == "async" and not self.persist:
            raise ValueError("async extraction requires persist=true")
        return self


class UsageResponse(StrictModel):
    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)
    total_tokens: int = Field(ge=0)


class ExtractionResponse(StrictModel):
    run_id: str | None
    status: Literal["queued", "running", "completed", "failed"]
    provider: str
    model: str
    result: ClinicalTrialEligibility | None
    usage: UsageResponse
    latency_ms: float | None
    estimated_cost_usd: float = Field(ge=0.0)


class EvaluationRequest(StrictModel):
    prediction: ClinicalTrialEligibility
    reference: ClinicalTrialEligibility
    persist: bool = True
    extraction_run_id: str | None = None

    @model_validator(mode="after")
    def trial_ids_match(self) -> EvaluationRequest:
        if self.prediction.trial_id != self.reference.trial_id:
            raise ValueError("prediction and reference trial_id values must match")
        return self


class EvaluationResponse(StrictModel):
    evaluation_id: str | None
    trial_id: str
    report: EvaluationReport


class BenchmarkCase(StrictModel):
    trial: TrialDocument
    reference: ClinicalTrialEligibility | None = None

    @model_validator(mode="after")
    def reference_matches_trial(self) -> BenchmarkCase:
        if self.reference is not None and self.reference.trial_id != self.trial.trial_id:
            raise ValueError("reference trial_id must match trial trial_id")
        return self


class BenchmarkRequest(StrictModel):
    cases: list[BenchmarkCase] = Field(min_length=1, max_length=100)
    persist: bool = True


class BenchmarkCaseResult(StrictModel):
    run_id: str | None
    trial_id: str
    extraction: ClinicalTrialEligibility
    evaluation: EvaluationReport | None
    latency_ms: float
    estimated_cost_usd: float = Field(ge=0.0)


class BenchmarkResponse(StrictModel):
    cases: list[BenchmarkCaseResult]
    evaluated_cases: int = Field(ge=0)
    mean_exact_match_f1: float | None = Field(default=None, ge=0.0, le=1.0)
    mean_token_f1: float | None = Field(default=None, ge=0.0, le=1.0)
    total_latency_ms: float = Field(ge=0.0)
    total_estimated_cost_usd: float = Field(ge=0.0)


class RunResponse(StrictModel):
    run_id: str
    trial_id: str
    trial_title: str
    provider: str
    model: str
    status: Literal["queued", "running", "completed", "failed"]
    result: ClinicalTrialEligibility | None
    error_code: str | None
    usage: UsageResponse
    latency_ms: float | None
    estimated_cost_usd: float = Field(ge=0.0)
    created_at: datetime
    started_at: datetime | None
    completed_at: datetime | None


class ServiceInfo(StrictModel):
    name: str
    version: str
    environment: str
    provider: str
    model: str
    paid_calls_enabled: bool
    credential_configured: bool
    authorization_guard_usd: float = Field(gt=0.0, le=2.0)
    input_cost_per_million_usd: float = Field(ge=0.0)
    output_cost_per_million_usd: float = Field(ge=0.0)
    schema_version: Literal["1.0"] = "1.0"


class ReadinessResponse(StrictModel):
    status: Literal["ready", "not_ready"]
    database: Literal["up", "down"]
    redis: Literal["up", "down", "not_required"]
