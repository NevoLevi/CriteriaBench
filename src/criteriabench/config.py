"""Typed application configuration with secret-safe defaults."""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import AliasChoices, Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

REVIEWED_LIVE_MODEL = "gpt-5.6-luna"
REVIEWED_INPUT_USD_PER_MILLION = 0.20
REVIEWED_OUTPUT_USD_PER_MILLION = 1.20


class Settings(BaseSettings):
    """Runtime settings with mock-only and bounded paid-use defaults."""

    model_config = SettingsConfigDict(
        env_prefix="CRITERIABENCH_",
        extra="ignore",
        case_sensitive=False,
    )

    app_name: str = "CriteriaBench"
    environment: Literal["local", "test", "staging", "production"] = "local"
    log_level: str = Field(
        default="INFO",
        validation_alias=AliasChoices("LOG_LEVEL", "CRITERIABENCH_LOG_LEVEL"),
    )
    api_prefix: str = "/api/v1"

    database_url: str = Field(
        default="sqlite+aiosqlite:///./criteriabench.db",
        validation_alias=AliasChoices("DATABASE_URL", "CRITERIABENCH_DATABASE_URL"),
    )
    redis_url: str = Field(
        default="redis://localhost:6379/0",
        validation_alias=AliasChoices("REDIS_URL", "CRITERIABENCH_REDIS_URL"),
    )
    auto_create_schema: bool = Field(
        default=True,
        validation_alias=AliasChoices(
            "AUTO_CREATE_SCHEMA",
            "CRITERIABENCH_AUTO_CREATE_SCHEMA",
        ),
    )
    readiness_requires_redis: bool = Field(
        default=False,
        validation_alias=AliasChoices(
            "READINESS_REQUIRES_REDIS",
            "CRITERIABENCH_READINESS_REQUIRES_REDIS",
        ),
    )

    provider: Literal["mock", "openai"] = Field(
        default="mock",
        validation_alias=AliasChoices("LLM_PROVIDER", "CRITERIABENCH_PROVIDER"),
    )
    allow_paid_calls: bool = Field(
        default=False,
        validation_alias=AliasChoices(
            "ALLOW_PAID_CALLS",
            "CRITERIABENCH_ALLOW_PAID_CALLS",
        ),
    )
    openai_api_key: SecretStr | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "OPENAI_API_KEY",
            "CRITERIABENCH_OPENAI_API_KEY",
        ),
    )
    openai_model: str = Field(
        default=REVIEWED_LIVE_MODEL,
        validation_alias=AliasChoices("OPENAI_MODEL", "CRITERIABENCH_OPENAI_MODEL"),
    )
    pricing_model: str = Field(
        default=REVIEWED_LIVE_MODEL,
        validation_alias=AliasChoices("PRICING_MODEL", "CRITERIABENCH_PRICING_MODEL"),
    )
    openai_timeout_seconds: float = Field(
        default=60.0,
        gt=0,
        le=300,
        allow_inf_nan=False,
    )
    openai_max_retries: int = Field(default=2, ge=0, le=5)
    max_output_tokens: int = Field(default=8_000, ge=1, le=100_000)
    estimated_input_tokens_per_request: int = Field(default=4_000, ge=1)
    live_run_budget_usd: float = Field(
        default=2.0,
        gt=0,
        allow_inf_nan=False,
        validation_alias=AliasChoices(
            "LIVE_RUN_BUDGET_USD",
            "CRITERIABENCH_LIVE_RUN_BUDGET_USD",
        ),
    )
    input_cost_per_million_usd: float = Field(
        default=REVIEWED_INPUT_USD_PER_MILLION,
        ge=0,
        allow_inf_nan=False,
        validation_alias=AliasChoices(
            "INPUT_COST_PER_MILLION_USD",
            "CRITERIABENCH_INPUT_COST_PER_MILLION_USD",
        ),
    )
    output_cost_per_million_usd: float = Field(
        default=REVIEWED_OUTPUT_USD_PER_MILLION,
        ge=0,
        allow_inf_nan=False,
        validation_alias=AliasChoices(
            "OUTPUT_COST_PER_MILLION_USD",
            "CRITERIABENCH_OUTPUT_COST_PER_MILLION_USD",
        ),
    )
    otel_exporter_otlp_endpoint: str | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "OTEL_EXPORTER_OTLP_ENDPOINT",
            "CRITERIABENCH_OTEL_EXPORTER_OTLP_ENDPOINT",
        ),
    )

    max_document_characters: int = Field(default=100_000, ge=1, le=100_000)
    max_batch_size: int = Field(default=100, ge=1, le=100)
    queue_name: str = Field(default="criteriabench:jobs", min_length=1, max_length=200)
    worker_poll_seconds: int = Field(default=5, ge=1, le=60)
    worker_metrics_port: int = Field(
        default=9_090,
        ge=1_024,
        le=65_535,
        validation_alias=AliasChoices(
            "WORKER_METRICS_PORT",
            "CRITERIABENCH_WORKER_METRICS_PORT",
        ),
    )

    @model_validator(mode="after")
    def validate_paid_configuration(self) -> Settings:
        if self.live_run_budget_usd > 2.0:
            raise ValueError("LIVE_RUN_BUDGET_USD exceeds the $2 safety ceiling")
        if self.provider == "openai" and self.allow_paid_calls:
            if (
                self.openai_model != REVIEWED_LIVE_MODEL
                or self.pricing_model != REVIEWED_LIVE_MODEL
            ):
                raise ValueError("paid mode is restricted to the reviewed gpt-5.6-luna price table")
            if (
                self.input_cost_per_million_usd != REVIEWED_INPUT_USD_PER_MILLION
                or self.output_cost_per_million_usd != REVIEWED_OUTPUT_USD_PER_MILLION
            ):
                raise ValueError("paid mode requires the reviewed Luna input/output rates")
        return self

    @property
    def key_is_configured(self) -> bool:
        """Return only a boolean; callers must never expose the secret value."""

        return self.openai_api_key is not None and bool(self.openai_api_key.get_secret_value())


@lru_cache
def get_settings() -> Settings:
    """Load and cache process-level settings without reading dotenv files."""

    return Settings()
