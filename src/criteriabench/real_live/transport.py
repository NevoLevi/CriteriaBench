"""One-attempt OpenAI Responses transport with sanitized, typed outcomes."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Protocol, cast

import httpx
from openai import AsyncOpenAI
from pydantic import BaseModel

from criteriabench.real_eval.integrity import canonical_sha256
from criteriabench.real_eval.models import GenerationCase
from criteriabench.real_live.contracts import (
    MAX_INPUT_TOKENS_RESERVED,
    CaseOutcomePayload,
    FrozenLunaConfiguration,
    SanitizedFailure,
    StrictOutputContract,
    UsageBreakdown,
    caller_execution_identity_sha256,
    frozen_execution_implementation,
    frozen_luna_configuration,
    price_usage,
    unavailable_usage,
)

OPENAI_ENVIRONMENT_OVERRIDES = frozenset(
    {
        "OPENAI_ADMIN_KEY",
        "OPENAI_ORG_ID",
        "OPENAI_PROJECT_ID",
        "OPENAI_WEBHOOK_SECRET",
        "OPENAI_BASE_URL",
        "OPENAI_CUSTOM_HEADERS",
        "OPENAI_API_TYPE",
        "OPENAI_API_VERSION",
        "AZURE_OPENAI_ENDPOINT",
        "AZURE_OPENAI_AD_TOKEN",
        "AZURE_OPENAI_API_KEY",
        "OPENAI_LOG",
    }
)
SAFE_RATE_LIMIT_CODES = frozenset(
    {
        "billing_hard_limit_reached",
        "billing_not_active",
        "insufficient_quota",
        "rate_limit_exceeded",
        "usage_limit_reached",
    }
)


def assert_clean_openai_environment(environ: Mapping[str, str]) -> None:
    """Reject SDK/endpoint/header overrides; OPENAI_API_KEY is the sole allowed input."""

    configured = sorted(name for name in OPENAI_ENVIRONMENT_OVERRIDES if environ.get(name))
    if configured:
        raise ValueError(
            "OpenAI/Azure SDK override environment variables must be unset for sealed live use"
        )


@dataclass(frozen=True, slots=True)
class StructuredCallSuccess[TOutput: BaseModel]:
    output: TOutput
    normalized_output: dict[str, object]
    normalized_output_sha256: str
    response_id_sha256: str
    usage: UsageBreakdown
    provider_model: str
    provider_model_sha256: str
    provider_response_object: str
    provider_response_object_sha256: str
    provider_service_tier: str
    provider_service_tier_sha256: str


@dataclass(frozen=True, slots=True)
class StructuredCallFailure:
    failure: SanitizedFailure
    response_id_sha256: str | None
    usage: UsageBreakdown
    provider_model: str | None = None
    provider_model_sha256: str | None = None
    provider_response_object: str | None = None
    provider_response_object_sha256: str | None = None
    provider_service_tier: str | None = None
    provider_service_tier_sha256: str | None = None


type StructuredCallResult[TOutput: BaseModel] = (
    StructuredCallSuccess[TOutput] | StructuredCallFailure
)


class StructuredCaller(Protocol):
    @property
    def execution_identity_sha256(self) -> str: ...

    async def call(
        self,
        case: GenerationCase,
        contract: StrictOutputContract[BaseModel],
    ) -> StructuredCallResult[BaseModel]: ...


class LunaResponsesCaller:
    """Fixed-endpoint, no-retry Luna caller.

    The production CLI passes one explicitly obtained process-scoped key only after
    every offline guard succeeds. All SDK override variables are rejected and the
    explicit HTTP client ignores proxy/certificate environment configuration.
    """

    def __init__(
        self,
        client: Any,
        luna: FrozenLunaConfiguration | None = None,
    ) -> None:
        self._client = client
        self._luna = luna or frozen_luna_configuration()
        implementation = frozen_execution_implementation()
        self._execution_identity_sha256 = caller_execution_identity_sha256(
            self._luna, implementation
        )

    @property
    def execution_identity_sha256(self) -> str:
        return self._execution_identity_sha256

    @classmethod
    def from_api_key(
        cls,
        api_key: str,
        luna: FrozenLunaConfiguration | None = None,
    ) -> LunaResponsesCaller:
        if not api_key:
            raise ValueError("an explicit API key is required for live execution")
        assert_clean_openai_environment(os.environ)
        sealed_luna = luna or frozen_luna_configuration()
        http_client = httpx.AsyncClient(
            timeout=sealed_luna.request_timeout_seconds,
            trust_env=sealed_luna.http_trust_env,
            follow_redirects=sealed_luna.follow_redirects,
        )
        return cls(
            AsyncOpenAI(
                api_key=api_key,
                base_url=sealed_luna.endpoint.removesuffix("/responses"),
                timeout=sealed_luna.request_timeout_seconds,
                max_retries=sealed_luna.sdk_max_retries,
                http_client=http_client,
            ),
            sealed_luna,
        )

    async def aclose(self) -> None:
        closer = getattr(self._client, "close", None)
        if closer is not None:
            result = closer()
            if hasattr(result, "__await__"):
                await result

    async def call(
        self,
        case: GenerationCase,
        contract: StrictOutputContract[BaseModel],
    ) -> StructuredCallResult[BaseModel]:
        request = build_responses_request(case, contract, luna=self._luna)
        _enforce_prompt_reservation(request)
        try:
            response = await self._client.responses.create(**request)
        except Exception as error:
            return StructuredCallFailure(
                failure=_failure_from_exception(error),
                response_id_sha256=None,
                usage=unavailable_usage(),
            )

        usage = usage_from_response(response)
        response_id_sha256 = _response_id_sha256(response)
        provider_model = _safe_string(_field(response, "model"))
        response_object = _safe_string(_field(response, "object"))
        service_tier = _safe_string(_field(response, "service_tier"))
        provider_model_label = _bounded_provider_identifier(provider_model)
        response_object_label = _bounded_provider_identifier(response_object)
        service_tier_label = _bounded_provider_identifier(service_tier)
        provider_model_sha256 = _optional_text_sha256(provider_model_label)
        response_object_sha256 = _optional_text_sha256(response_object_label)
        service_tier_sha256 = _optional_text_sha256(service_tier_label)

        def response_failure(
            kind: str,
            *,
            retryable: bool,
            safe_code: object | None = None,
        ) -> StructuredCallFailure:
            return _response_failure(
                kind,
                retryable=retryable,
                safe_code=safe_code,
                response_id_sha256=response_id_sha256,
                usage=usage,
                provider_model=provider_model_label,
                provider_model_sha256=provider_model_sha256,
                response_object=response_object_label,
                response_object_sha256=response_object_sha256,
                service_tier=service_tier_label,
                service_tier_sha256=service_tier_sha256,
            )

        if provider_model != self._luna.model:
            return response_failure("model_mismatch", retryable=False)
        if response_object != "response" or response_id_sha256 is None:
            return response_failure("response_contract", retryable=False)
        if service_tier != self._luna.service_tier:
            return response_failure(
                "response_contract",
                retryable=False,
                safe_code="unexpected_service_tier",
            )

        status = _safe_string(_field(response, "status"))
        if status == "incomplete":
            reason = _safe_string(_field(_field(response, "incomplete_details"), "reason"))
            kind = "truncated_output" if reason == "max_output_tokens" else "content_filter"
            return response_failure(kind, retryable=False, safe_code=reason)
        if status in {"failed", "cancelled"}:
            return response_failure(
                _provider_response_failure_kind(response),
                retryable=False,
            )
        if status != "completed":
            return response_failure(
                "response_contract",
                retryable=False,
                safe_code="non_completed_status",
            )
        if _field(response, "error") is not None:
            return response_failure(
                _provider_response_failure_kind(response),
                retryable=False,
            )
        if _has_refusal(response):
            return response_failure("refusal", retryable=False)

        output_text = _field(response, "output_text")
        if not isinstance(output_text, str) or not output_text:
            return response_failure("response_contract", retryable=False)
        try:
            decoded = json.loads(output_text)
        except (UnicodeDecodeError, json.JSONDecodeError):
            return response_failure("invalid_json", retryable=False)
        if not isinstance(decoded, dict) or any(not isinstance(key, str) for key in decoded):
            return response_failure("schema_validation", retryable=False)
        try:
            payload = cast(dict[str, object], decoded)
            parsed = contract.parse(payload)
            normalized = parsed.model_dump(mode="json")
            if not isinstance(normalized, dict):
                raise ValueError("normalized structured output is not an object")
            normalized_output = cast(dict[str, object], normalized)
            normalized_sha256 = canonical_sha256(normalized_output)
        except Exception as error:
            return response_failure(
                "schema_validation",
                retryable=False,
                safe_code=type(error).__name__,
            )
        return StructuredCallSuccess(
            output=parsed,
            normalized_output=normalized_output,
            normalized_output_sha256=normalized_sha256,
            response_id_sha256=response_id_sha256,
            usage=usage,
            provider_model=self._luna.model,
            provider_model_sha256=hashlib.sha256(self._luna.model.encode()).hexdigest(),
            provider_response_object="response",
            provider_response_object_sha256=hashlib.sha256(b"response").hexdigest(),
            provider_service_tier=self._luna.service_tier,
            provider_service_tier_sha256=hashlib.sha256(
                self._luna.service_tier.encode()
            ).hexdigest(),
        )


def build_responses_request(
    case: GenerationCase,
    contract: StrictOutputContract[BaseModel],
    *,
    luna: FrozenLunaConfiguration | None = None,
) -> dict[str, object]:
    """Serialize only criterion polarity/kind and source text for the provider."""

    sealed_luna = luna or frozen_luna_configuration()
    provider_input = {
        "criterion_kind": case.criterion_kind.value,
        "criterion_text": case.source_text,
    }
    return {
        "model": sealed_luna.model,
        "instructions": contract.instructions,
        "input": json.dumps(
            provider_input,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ),
        "reasoning": {"effort": sealed_luna.reasoning_effort},
        "text": {
            "format": {
                "type": "json_schema",
                "name": contract.schema_name,
                "schema": contract.schema(),
                "strict": True,
            }
        },
        "max_output_tokens": sealed_luna.max_output_tokens,
        "service_tier": sealed_luna.service_tier,
        "tools": list(sealed_luna.tools),
        "store": sealed_luna.store,
    }


def request_sha256(
    case: GenerationCase,
    contract: StrictOutputContract[BaseModel],
    *,
    luna: FrozenLunaConfiguration | None = None,
) -> str:
    return canonical_sha256(build_responses_request(case, contract, luna=luna))


def usage_from_response(response: object) -> UsageBreakdown:
    usage = _field(response, "usage")
    input_tokens = _safe_nonnegative_int(_field(usage, "input_tokens"))
    output_tokens = _safe_nonnegative_int(_field(usage, "output_tokens"))
    if input_tokens is None or output_tokens is None:
        return unavailable_usage()
    details = _field(usage, "input_tokens_details")
    cached_tokens = _safe_nonnegative_int(_field(details, "cached_tokens"))
    if cached_tokens is None:
        cached_tokens = 0
    cache_write_tokens = _safe_nonnegative_int(_field(details, "cache_write_tokens"))
    if cache_write_tokens is None:
        cache_write_tokens = _safe_nonnegative_int(_field(details, "cache_creation_tokens"))
    if cache_write_tokens is None:
        cache_write_tokens = 0
    if cached_tokens + cache_write_tokens > input_tokens:
        return unavailable_usage()
    uncached_tokens = input_tokens - cached_tokens - cache_write_tokens
    costs = price_usage(
        uncached_input_tokens=uncached_tokens,
        cached_input_tokens=cached_tokens,
        cache_write_input_tokens=cache_write_tokens,
        output_tokens=output_tokens,
    )
    return UsageBreakdown(
        availability="complete",
        input_tokens=input_tokens,
        uncached_input_tokens=uncached_tokens,
        cached_input_tokens=cached_tokens,
        cache_write_input_tokens=cache_write_tokens,
        output_tokens=output_tokens,
        **costs,
    )


def outcome_payload(
    *,
    plan_sha256: str,
    ordinal: int,
    case: GenerationCase,
    request_digest: str,
    attempt_digest: str,
    external_attempt_claim_digest: str,
    outcome_finished_at_utc: str,
    total_latency_ms: int | None,
    result: StructuredCallResult[BaseModel],
    reservation_usd: str,
) -> CaseOutcomePayload:
    """Convert a bounded caller result into a sealed-artifact payload."""

    charged = (
        reservation_usd
        if result.usage.availability == "unavailable"
        else result.usage.total_cost_usd
    )
    common: dict[str, object] = {
        "schema_version": "real-live-case-outcome-v1",
        "plan_sha256": plan_sha256,
        "ordinal": ordinal,
        "case_id": case.case_id,
        "trial_id": case.trial_id,
        "document_id": case.document_id,
        "source_sha256": case.source_sha256,
        "request_sha256": request_digest,
        "attempt_sha256": attempt_digest,
        "external_attempt_claim_sha256": external_attempt_claim_digest,
        "outcome_finished_at_utc": outcome_finished_at_utc,
        "total_latency_ms": total_latency_ms,
        "usage": result.usage,
        "charged_cost_usd": charged,
        "response_id_sha256": result.response_id_sha256,
        "provider_model": result.provider_model,
        "provider_model_sha256": result.provider_model_sha256,
        "provider_response_object": result.provider_response_object,
        "provider_response_object_sha256": result.provider_response_object_sha256,
        "provider_service_tier": result.provider_service_tier,
        "provider_service_tier_sha256": result.provider_service_tier_sha256,
    }
    if Decimal(charged) > Decimal(reservation_usd):
        return CaseOutcomePayload.model_validate(
            {
                **common,
                "status": "failed",
                "normalized_output_sha256": None,
                "normalized_output": None,
                "failure": _sanitized_failure(
                    "budget_breach",
                    retryable=False,
                    safe_code="known_usage_exceeds_reservation",
                ),
            }
        )
    if isinstance(result, StructuredCallSuccess):
        return CaseOutcomePayload.model_validate(
            {
                **common,
                "status": "completed",
                "normalized_output_sha256": result.normalized_output_sha256,
                "normalized_output": result.normalized_output,
                "failure": None,
            }
        )
    return CaseOutcomePayload.model_validate(
        {
            **common,
            "status": "failed",
            "normalized_output_sha256": None,
            "normalized_output": None,
            "failure": result.failure,
        }
    )


def _enforce_prompt_reservation(request: Mapping[str, object]) -> None:
    """Fail before the network if serialized prompt material exceeds 16K tokens.

    A byte is a strict upper bound on byte-pair tokens for the submitted UTF-8
    material.  This intentionally counts the entire request JSON, including the
    strict schema, rather than estimating source text alone.
    """

    serialized = json.dumps(
        request,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    if len(serialized) > MAX_INPUT_TOKENS_RESERVED:
        raise ValueError("serialized request exceeds the conservative 16K input reservation")


def _failure_from_exception(error: Exception) -> SanitizedFailure:
    status_code = _safe_nonnegative_int(getattr(error, "status_code", None))
    error_code = _safe_string(getattr(error, "code", None))
    if isinstance(error, (TimeoutError, asyncio.TimeoutError)) or status_code in {408, 504}:
        return _sanitized_failure("timeout", retryable=True, safe_code=status_code)
    if status_code == 401:
        return _sanitized_failure("authentication", retryable=False, safe_code=status_code)
    if status_code == 403:
        return _sanitized_failure("authorization", retryable=False, safe_code=status_code)
    if status_code == 404:
        return _sanitized_failure("model_not_found", retryable=False, safe_code=status_code)
    if status_code in {400, 422}:
        return _sanitized_failure("request_configuration", retryable=False, safe_code=status_code)
    if status_code == 429:
        safe_code: object = error_code if error_code in SAFE_RATE_LIMIT_CODES else status_code
        return _sanitized_failure("rate_limit", retryable=False, safe_code=safe_code)
    type_name = type(error).__name__
    if type_name in {
        "APIConnectionError",
        "ConnectError",
        "ConnectionError",
        "NetworkError",
    }:
        return _sanitized_failure("network", retryable=True, safe_code=type_name)
    retryable = status_code is not None and status_code >= 500
    return _sanitized_failure(
        "provider_error",
        retryable=retryable,
        safe_code={"exception_type": type_name, "status_code": status_code},
    )


def _sanitized_failure(
    kind: str,
    *,
    retryable: bool,
    safe_code: object | None = None,
) -> SanitizedFailure:
    payload = {"kind": kind, "retryable": retryable, "safe_code": safe_code}
    return SanitizedFailure.model_validate(
        {
            "kind": kind,
            "retryable": retryable,
            "fingerprint_sha256": canonical_sha256(payload),
        }
    )


def _response_failure(
    kind: str,
    *,
    retryable: bool,
    response_id_sha256: str | None,
    usage: UsageBreakdown,
    provider_model: str | None,
    provider_model_sha256: str | None,
    response_object: str | None,
    response_object_sha256: str | None,
    service_tier: str | None,
    service_tier_sha256: str | None,
    safe_code: object | None = None,
) -> StructuredCallFailure:
    return StructuredCallFailure(
        failure=_sanitized_failure(kind, retryable=retryable, safe_code=safe_code),
        response_id_sha256=response_id_sha256,
        usage=usage,
        provider_model=provider_model,
        provider_model_sha256=provider_model_sha256,
        provider_response_object=response_object,
        provider_response_object_sha256=response_object_sha256,
        provider_service_tier=service_tier,
        provider_service_tier_sha256=service_tier_sha256,
    )


def _provider_response_failure_kind(response: object) -> str:
    error = _field(response, "error")
    code = _safe_string(_field(error, "code"))
    if code == "model_not_found":
        return "model_not_found"
    if code in SAFE_RATE_LIMIT_CODES:
        return "rate_limit"
    if code is not None and (
        code.startswith("invalid_")
        or code in {"unsupported_parameter", "bad_request", "context_length_exceeded"}
    ):
        return "request_configuration"
    return "provider_error"


def _response_id_sha256(response: object) -> str | None:
    response_id = _field(response, "id")
    if not isinstance(response_id, str) or not response_id:
        return None
    return hashlib.sha256(response_id.encode("utf-8")).hexdigest()


def _optional_text_sha256(value: str | None) -> str | None:
    if value is None:
        return None
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _bounded_provider_identifier(value: str | None) -> str | None:
    if value is None or not (1 <= len(value) <= 128):
        return None
    allowed = frozenset("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789._:/-")
    if value[0].isalnum() and all(character in allowed for character in value):
        return value
    return None


def _has_refusal(response: object) -> bool:
    stack: list[object] = [_field(response, "output")]
    while stack:
        value = stack.pop()
        if isinstance(value, Mapping):
            if value.get("type") == "refusal":
                return True
            stack.extend(value.values())
        elif isinstance(value, (list, tuple)):
            stack.extend(value)
        elif value is not None and not isinstance(value, (str, bytes, int, float, bool)):
            item_type = getattr(value, "type", None)
            if item_type == "refusal":
                return True
            content = getattr(value, "content", None)
            if content is not None:
                stack.append(content)
    return False


def _field(value: object, name: str) -> object | None:
    if isinstance(value, Mapping):
        return value.get(name)
    if value is None:
        return None
    return getattr(value, name, None)


def _safe_nonnegative_int(value: object) -> int | None:
    if type(value) is not int or value < 0:
        return None
    return value


def _safe_string(value: object) -> str | None:
    return value if isinstance(value, str) else None
