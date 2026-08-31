from __future__ import annotations

import pytest

from criteriabench.config import Settings
from criteriabench.providers.factory import create_provider


def test_settings_never_implicitly_load_dotenv_files() -> None:
    assert Settings.model_config.get("env_file") in (None, ())


def test_mock_is_default_and_key_presence_does_not_enable_paid_calls() -> None:
    settings = Settings(_env_file=None, OPENAI_API_KEY="unit-test-placeholder")
    assert settings.provider == "mock"
    assert settings.allow_paid_calls is False
    assert create_provider(settings).name == "mock"


def test_openai_provider_requires_independent_paid_call_gate() -> None:
    settings = Settings(
        _env_file=None,
        LLM_PROVIDER="openai",
        ALLOW_PAID_CALLS=False,
        OPENAI_API_KEY="unit-test-placeholder",
    )
    with pytest.raises(RuntimeError, match="ALLOW_PAID_CALLS"):
        create_provider(settings)


def test_infrastructure_aliases_are_supported() -> None:
    settings = Settings(
        _env_file=None,
        DATABASE_URL="sqlite+aiosqlite:///:memory:",
        REDIS_URL="redis://example.invalid:6379/2",
        LLM_PROVIDER="mock",
        OTEL_EXPORTER_OTLP_ENDPOINT="http://otel-collector:4317",
    )
    assert settings.database_url.endswith(":memory:")
    assert settings.redis_url.endswith("/2")
    assert settings.otel_exporter_otlp_endpoint == "http://otel-collector:4317"
