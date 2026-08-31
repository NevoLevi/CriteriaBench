"""Stable extraction contracts used to fail closed across worker rollouts."""

from __future__ import annotations

import hashlib
import inspect
import json

import criteriabench.providers.mock as mock_provider_module
import criteriabench.providers.openai as openai_provider_module
from criteriabench import __version__
from criteriabench.domain.schemas import ClinicalTrialEligibility

SCHEMA_VERSION = "1.0"


def extraction_contract_hash(*, provider: str, model: str) -> str:
    """Hash the schema and exact provider implementation used by an async job.

    The HTTP/worker path is mock-only, but including the OpenAI adapter and its
    instructions keeps the helper fail-closed if that policy ever changes.
    Runtime pricing and retry configuration belong to live CLI artifacts because
    paid jobs are deliberately never queued.
    """

    implementation = _provider_implementation(provider)
    canonical = json.dumps(
        {
            "application_version": __version__,
            "provider": provider,
            "model": model,
            "schema_version": SCHEMA_VERSION,
            "schema": ClinicalTrialEligibility.model_json_schema(),
            "implementation_sha256": hashlib.sha256(implementation.encode("utf-8")).hexdigest(),
        },
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _provider_implementation(provider: str) -> str:
    if provider == "mock":
        return inspect.getsource(mock_provider_module)
    if provider == "openai":
        return inspect.getsource(openai_provider_module)
    raise ValueError(f"unsupported extraction provider: {provider}")
