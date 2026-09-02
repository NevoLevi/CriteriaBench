from __future__ import annotations

import argparse
import asyncio
import hashlib
import io
import json
import shutil
import sys
from datetime import UTC, datetime
from decimal import Decimal
from functools import lru_cache
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from pydantic import BaseModel

from criteriabench.real.llf import load_llf_records
from criteriabench.real.llf_semantics import LlfSemanticOutput
from criteriabench.real_eval import llf_canary_preregistration as canary_prereg
from criteriabench.real_eval.integrity import canonical_sha256, case_set_sha256, source_sha256
from criteriabench.real_eval.llf_binding import load_llf_generation_split
from criteriabench.real_eval.models import GenerationCase, GenerationDatasetBinding
from criteriabench.real_live import cli
from criteriabench.real_live import contracts as live_contracts
from criteriabench.real_live import runner as live_runner
from criteriabench.real_live import transport as live_transport
from criteriabench.real_live.contracts import (
    GRAPH_V2_SCHEMA_SHA256,
    LLF_CANARY_ACKNOWLEDGEMENT,
    LLF_DEVELOPMENT_BARE_SYMBOLS,
    LLF_DEVELOPMENT_DIRECT_CALLS,
    LLF_DEVELOPMENT_METHOD_ATTRIBUTES,
    LLF_ENGINEERING_LIMITS,
    LLF_ENGINEERING_LIMITS_SHA256,
    LLF_PROMPT_EXAMPLE_TRIAL_IDS,
    LLF_PROMPT_EXAMPLES,
    LLF_PROMPT_EXAMPLES_SHA256,
    LLF_WIRE_SCHEMA_SHA256,
    LOCKED_ACKNOWLEDGEMENT,
    LOCKED_BUDGET_CAP_USD,
    RESERVATION_PER_CASE_USD,
    CanaryExecutionBinding,
    CaseOutcome,
    LivePlan,
    PaidAuthorization,
    PendingAttempt,
    SanitizedFailure,
    StrictOutputContract,
    UsageBreakdown,
    caller_execution_identity_sha256,
    frozen_execution_implementation,
    frozen_luna_configuration,
    graph_v2_output_contract,
    llf_semantic_output_contract,
    money,
    price_usage,
    unavailable_usage,
)
from criteriabench.real_live.planning import (
    authorize_plan,
    build_graph_product_canary_plan,
    build_llf_canary_plan,
    build_locked_llf_plan,
    run_directory_sha256,
    select_development_canary,
)
from criteriabench.real_live.runner import (
    AuthorizationClaimStore,
    FatalProviderConfigurationError,
    LiveRunError,
    RunArtifactStore,
    _seal_pending,
    recover_live_run,
    run_live_plan,
)
from criteriabench.real_live.transport import (
    LunaResponsesCaller,
    StructuredCallFailure,
    StructuredCallSuccess,
    build_responses_request,
    usage_from_response,
)

ROOT = Path(__file__).resolve().parents[1]
LLF_DATA = ROOT / "data" / "real" / "llf"
COVERAGE_DIR = ROOT / "docs" / "results"
NOW = "2026-09-02T12:00:00Z"
FIXED_NOW = datetime(2026, 9, 2, 13, 0, 0, tzinfo=UTC)
TEST_IMAGE_ID = "sha256:" + ("d" * 64)
RUNTIME_OUTPUT_DIRECTORY = "/run/artifacts/output"
RUNTIME_OUTPUT_DIRECTORY_SHA256 = run_directory_sha256(RUNTIME_OUTPUT_DIRECTORY)


def _fixed_clock() -> datetime:
    return FIXED_NOW


def _case(index: int, *, trial_id: str | None = None) -> GenerationCase:
    kind = "inclusion" if index % 2 == 0 else "exclusion"
    length = 25 + (index % 9) * 30
    text = f"Criterion {index}: " + ("x" * length)
    return GenerationCase(
        case_id=f"CASE-{index:04d}",
        trial_id=trial_id or f"NCT{index:08d}",
        document_id=f"source-{index:04d}.js",
        criterion_kind=kind,
        source_text=text,
        source_sha256=source_sha256(text),
    )


def _cases(count: int) -> tuple[GenerationCase, ...]:
    return tuple(_case(index) for index in range(count))


def _dataset(
    cases: tuple[GenerationCase, ...],
    *,
    split: str,
    dataset_id: str = "test-real-data",
    dataset_version: str = "v1",
) -> GenerationDatasetBinding:
    return GenerationDatasetBinding(
        dataset_id=dataset_id,
        dataset_version=dataset_version,
        split=split,
        split_unit="trial_id",
        generation_manifest_sha256="a" * 64,
        generation_cases_sha256="b" * 64,
        split_assignments_sha256="c" * 64,
        case_set_sha256=case_set_sha256(cases),
        case_count=len(cases),
    )


def _canary(
    run_directory: Path,
) -> tuple[
    LivePlan,
    tuple[GenerationCase, ...],
    PaidAuthorization,
    CanaryExecutionBinding,
    StrictOutputContract[LlfSemanticOutput],
]:
    generation = load_llf_generation_split(LLF_DATA, "development")
    contract = llf_semantic_output_contract()
    plan, selected = build_llf_canary_plan(
        generation.cases,
        dataset=generation.dataset,
        contract=contract,
        created_at_utc=NOW,
        runtime_image_id=TEST_IMAGE_ID,
    )
    state_directory = _state_directory(run_directory)
    preregistration = _test_preregistration()
    preregistration_path = _preregistration_path(run_directory)
    binding = canary_prereg.build_canary_execution_binding(
        preregistration=preregistration,
        plan=plan,
        plan_artifact_sha256=hashlib.sha256(live_runner._model_bytes(plan)).hexdigest(),
        intended_run_id=run_directory.name,
        intended_authorization_id="auth-canary-test",
        host_output_directory_sha256=run_directory_sha256(run_directory),
        authorization_state_directory_sha256=run_directory_sha256(state_directory),
    )
    authorization = authorize_plan(
        plan,
        preregistration_path=preregistration_path,
        execution_binding=binding,
        authorization_id="auth-canary-test",
        authorized_at_utc=NOW,
        run_directory=RUNTIME_OUTPUT_DIRECTORY,
        host_run_directory_sha256=run_directory_sha256(run_directory),
        authorization_state_directory_sha256=run_directory_sha256(state_directory),
        run_id=run_directory.name,
        acknowledgement=LLF_CANARY_ACKNOWLEDGEMENT,
    )
    return plan, selected, authorization, binding, contract


@lru_cache(maxsize=1)
def _test_preregistration() -> canary_prereg.CanaryPreregistration:
    return canary_prereg.build_llf_canary_preregistration(
        dataset_dir=LLF_DATA,
        coverage_dir=COVERAGE_DIR,
    )


def _preregistration_path(run_directory: Path) -> Path:
    public_directory = run_directory.parent / f"public-{run_directory.name}"
    public_directory.mkdir(parents=True, exist_ok=True)
    path = public_directory / "llf-canary-preregistration.json"
    payload = canary_prereg.preregistration_bytes(_test_preregistration())
    if path.exists():
        if path.read_bytes() != payload:
            raise AssertionError("test preregistration artifact changed unexpectedly")
    else:
        path.write_bytes(payload)
    return path


def _state_directory(run_directory: Path) -> Path:
    state = run_directory.parent / ".real-live-authorization-state"
    state.mkdir(parents=True, exist_ok=True)
    return state


def _live_scope(
    run_directory: Path,
    binding: CanaryExecutionBinding,
) -> dict[str, object]:
    state_directory = _state_directory(run_directory)
    return {
        "execution_binding": binding,
        "preregistration_path": _preregistration_path(run_directory),
        "output_dir": run_directory,
        "authorization_state_dir": state_directory,
        "runtime_output_directory_sha256": RUNTIME_OUTPUT_DIRECTORY_SHA256,
        "host_run_directory_sha256": run_directory_sha256(run_directory),
        "authorization_state_directory_sha256": run_directory_sha256(state_directory),
        "run_id": run_directory.name,
        "runtime_image_id": TEST_IMAGE_ID,
    }


def _forged_execution_binding(
    binding: CanaryExecutionBinding,
    *,
    attack: str,
) -> CanaryExecutionBinding:
    body = binding.model_dump(mode="json", exclude={"execution_binding_sha256"})
    if attack == "preregistration-hash":
        body["preregistration_sha256"] = "0" * 64
    elif attack == "advancement-gates-hash":
        body["advancement_gates_sha256"] = "0" * 64
    elif attack == "quality-failure-policy":
        body["quality_failure_policy"] = "select_the_best_nondeterministic_result"
    else:
        raise AssertionError(f"unknown forged-binding attack: {attack}")
    body["execution_binding_sha256"] = canonical_sha256(
        {key: value for key, value in body.items() if key != "execution_binding_sha256"}
    )
    if attack == "quality-failure-policy":
        # A hostile Python caller can bypass Pydantic construction with model_construct;
        # the paid core must still reproduce and reject the public chain itself.
        return CanaryExecutionBinding.model_construct(**body)
    return CanaryExecutionBinding.model_validate(body)


def _forged_preregistration_path(run_directory: Path) -> Path:
    body = _test_preregistration().model_dump(
        mode="json",
        exclude={"preregistration_sha256"},
    )
    advancement_gates = body["advancement_gates"]
    assert isinstance(advancement_gates, dict)
    advancement_gates["maximum_p95_latency_ms"] = 59_000.0
    body["preregistration_sha256"] = canonical_sha256(
        {key: value for key, value in body.items() if key != "preregistration_sha256"}
    )
    forged = canary_prereg.CanaryPreregistration.model_validate(body)
    public_directory = run_directory.parent / f"forged-public-{run_directory.name}"
    public_directory.mkdir(parents=True, exist_ok=True)
    path = public_directory / "llf-canary-preregistration.json"
    path.write_bytes(canary_prereg.preregistration_bytes(forged))
    return path


def _assert_no_paid_artifacts(run_directory: Path, state_directory: Path) -> None:
    assert not (run_directory / "authorization-consumed.json").exists()
    assert not (run_directory / "pending.json").exists()
    assert not tuple(run_directory.glob("attempt-*.json"))
    assert not tuple(run_directory.glob("case-*.json"))
    assert not (run_directory / "summary.json").exists()
    assert not tuple(state_directory.glob("claim-*.json"))
    assert not tuple(state_directory.glob("attempt-*.json"))


def _rewrite_case_outcome(path: Path, **changes: object) -> CaseOutcome:
    body = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(body, dict)
    body.update(changes)
    body.pop("outcome_sha256")
    body["outcome_sha256"] = canonical_sha256(body)
    outcome = CaseOutcome.model_validate(body)
    path.write_bytes(live_runner._model_bytes(outcome))
    return outcome


def _rewrite_pending_attempt(path: Path, **changes: object) -> PendingAttempt:
    body = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(body, dict)
    body.update(changes)
    body.pop("pending_sha256")
    body["pending_sha256"] = canonical_sha256(body)
    pending = PendingAttempt.model_validate(body)
    path.write_bytes(live_runner._model_bytes(pending))
    return pending


def _known_usage() -> UsageBreakdown:
    costs = price_usage(
        uncached_input_tokens=100,
        cached_input_tokens=20,
        cache_write_input_tokens=10,
        output_tokens=30,
    )
    return UsageBreakdown(
        availability="complete",
        input_tokens=130,
        uncached_input_tokens=100,
        cached_input_tokens=20,
        cache_write_input_tokens=10,
        output_tokens=30,
        **costs,
    )


class SuccessfulCaller:
    def __init__(self) -> None:
        self.calls: list[str] = []
        self.active = 0
        self.maximum_active = 0

    @property
    def execution_identity_sha256(self) -> str:
        return caller_execution_identity_sha256(
            frozen_luna_configuration(), frozen_execution_implementation()
        )

    async def call(
        self,
        case: GenerationCase,
        contract: StrictOutputContract[BaseModel],
    ) -> StructuredCallSuccess[BaseModel]:
        self.calls.append(case.case_id)
        self.active += 1
        self.maximum_active = max(self.maximum_active, self.active)
        await asyncio.sleep(0)
        output = contract.parse({"logical_form": 'cond("x")'})
        self.active -= 1
        normalized = output.model_dump(mode="json")
        return StructuredCallSuccess(
            output=output,
            normalized_output=normalized,
            normalized_output_sha256=canonical_sha256(normalized),
            response_id_sha256=hashlib.sha256(f"response:{case.case_id}".encode()).hexdigest(),
            usage=_known_usage(),
            provider_model="gpt-5.6-luna",
            provider_model_sha256=hashlib.sha256(b"gpt-5.6-luna").hexdigest(),
            provider_response_object="response",
            provider_response_object_sha256=hashlib.sha256(b"response").hexdigest(),
            provider_service_tier="default",
            provider_service_tier_sha256=hashlib.sha256(b"default").hexdigest(),
        )


class CallerInteractionProbe(SuccessfulCaller):
    def __init__(self) -> None:
        super().__init__()
        self.execution_identity_reads = 0

    @property
    def execution_identity_sha256(self) -> str:
        self.execution_identity_reads += 1
        return super().execution_identity_sha256


class NeverCall:
    @property
    def execution_identity_sha256(self) -> str:
        return caller_execution_identity_sha256(
            frozen_luna_configuration(), frozen_execution_implementation()
        )

    async def call(
        self,
        case: GenerationCase,
        contract: StrictOutputContract[BaseModel],
    ) -> StructuredCallSuccess[BaseModel] | StructuredCallFailure:
        raise AssertionError("resume/replay guard must prevent another provider call")


class FatalCaller:
    def __init__(self, kind: str) -> None:
        self.calls: list[str] = []
        self.kind = kind

    @property
    def execution_identity_sha256(self) -> str:
        return caller_execution_identity_sha256(
            frozen_luna_configuration(), frozen_execution_implementation()
        )

    async def call(
        self,
        case: GenerationCase,
        contract: StrictOutputContract[BaseModel],
    ) -> StructuredCallFailure:
        del contract
        self.calls.append(case.case_id)
        return StructuredCallFailure(
            failure=SanitizedFailure(
                kind=self.kind,
                retryable=False,
                fingerprint_sha256=canonical_sha256({"fatal_test_kind": self.kind}),
            ),
            response_id_sha256=None,
            usage=unavailable_usage(),
        )


class HighUsageCaller(SuccessfulCaller):
    async def call(
        self,
        case: GenerationCase,
        contract: StrictOutputContract[BaseModel],
    ) -> StructuredCallSuccess[BaseModel]:
        self.calls.append(case.case_id)
        output = contract.parse({"logical_form": 'cond("x")'})
        normalized = output.model_dump(mode="json")
        costs = price_usage(
            uncached_input_tokens=0,
            cached_input_tokens=0,
            cache_write_input_tokens=0,
            output_tokens=6_000,
        )
        usage = UsageBreakdown(
            availability="complete",
            input_tokens=0,
            uncached_input_tokens=0,
            cached_input_tokens=0,
            cache_write_input_tokens=0,
            output_tokens=6_000,
            **costs,
        )
        return StructuredCallSuccess(
            output=output,
            normalized_output=normalized,
            normalized_output_sha256=canonical_sha256(normalized),
            response_id_sha256="b" * 64,
            usage=usage,
            provider_model="gpt-5.6-luna",
            provider_model_sha256=hashlib.sha256(b"gpt-5.6-luna").hexdigest(),
            provider_response_object="response",
            provider_response_object_sha256=hashlib.sha256(b"response").hexdigest(),
            provider_service_tier="default",
            provider_service_tier_sha256=hashlib.sha256(b"default").hexdigest(),
        )


class MalformedAncillaryCaller(SuccessfulCaller):
    async def call(
        self,
        case: GenerationCase,
        contract: StrictOutputContract[BaseModel],
    ) -> StructuredCallSuccess[BaseModel]:
        result = await super().call(case, contract)
        return StructuredCallSuccess(
            output=result.output,
            normalized_output=result.normalized_output,
            normalized_output_sha256=result.normalized_output_sha256,
            response_id_sha256="not-a-digest",
            usage=result.usage,
            provider_model=result.provider_model,
            provider_model_sha256=result.provider_model_sha256,
            provider_response_object=result.provider_response_object,
            provider_response_object_sha256=result.provider_response_object_sha256,
            provider_service_tier=result.provider_service_tier,
            provider_service_tier_sha256=result.provider_service_tier_sha256,
        )


class DuplicateResponseIdCaller(SuccessfulCaller):
    async def call(
        self,
        case: GenerationCase,
        contract: StrictOutputContract[BaseModel],
    ) -> StructuredCallSuccess[BaseModel]:
        result = await super().call(case, contract)
        return StructuredCallSuccess(
            output=result.output,
            normalized_output=result.normalized_output,
            normalized_output_sha256=result.normalized_output_sha256,
            response_id_sha256="d" * 64,
            usage=result.usage,
            provider_model=result.provider_model,
            provider_model_sha256=result.provider_model_sha256,
            provider_response_object=result.provider_response_object,
            provider_response_object_sha256=result.provider_response_object_sha256,
            provider_service_tier=result.provider_service_tier,
            provider_service_tier_sha256=result.provider_service_tier_sha256,
        )


class EscapingCaller(SuccessfulCaller):
    async def call(
        self,
        case: GenerationCase,
        contract: StrictOutputContract[BaseModel],
    ) -> StructuredCallSuccess[BaseModel]:
        del contract
        self.calls.append(case.case_id)
        raise RuntimeError("private escaped SDK/programming detail")


class HangingCaller(SuccessfulCaller):
    async def call(
        self,
        case: GenerationCase,
        contract: StrictOutputContract[BaseModel],
    ) -> StructuredCallSuccess[BaseModel]:
        del contract
        self.calls.append(case.case_id)
        await asyncio.Event().wait()
        raise AssertionError("unreachable after cancellation")


def test_wire_schema_is_frozen_strict_compact_and_identity_free() -> None:
    contract = llf_semantic_output_contract()
    schema = contract.schema()
    serialized = json.dumps(schema, sort_keys=True, separators=(",", ":"))
    properties = schema["properties"]
    assert isinstance(properties, dict)

    assert contract.schema_sha256 == LLF_WIRE_SCHEMA_SHA256
    assert set(properties) == {"logical_form"}
    assert schema["required"] == ["logical_form"]
    assert schema["additionalProperties"] is False
    logical_form_schema = properties["logical_form"]
    assert isinstance(logical_form_schema, dict)
    assert logical_form_schema == {"type": "string"}
    assert "minLength" not in serialized
    assert "maxLength" not in serialized
    for forbidden in (
        "case_id",
        "trial_id",
        "document_id",
        "source_sha256",
        "reference",
        "source_path",
    ):
        assert forbidden not in serialized
    parser_file = ROOT / "src" / "criteriabench" / "real_live" / "contracts.py"
    assert contract.parser_code_sha256 == hashlib.sha256(parser_file.read_bytes()).hexdigest()


def test_wire_parser_uses_only_predeclared_engineering_limits() -> None:
    assert LLF_ENGINEERING_LIMITS == {
        "policy_id": "llf-live-engineering-limits-v1",
        "logical_form_characters": 8_192,
        "logical_form_utf8_bytes": 16_384,
        "semantic_nodes": 256,
        "semantic_depth": 64,
        "call_arguments": 32,
        "collection_items": 32,
        "identifier_characters": 128,
        "string_utf8_bytes": 1_024,
    }
    assert (
        LLF_ENGINEERING_LIMITS_SHA256
        == "0ed21cce49d625018b0f26f5fb4b27667d8e9757e38755f7f17e8c4aee7dff52"
    )
    logical_form = "union(" + ",".join(f'cond("x{index}")' for index in range(40)) + ")"
    with pytest.raises(ValueError, match="predeclared call-argument limit"):
        llf_semantic_output_contract().parse({"logical_form": logical_form})


def test_prompt_vocabulary_is_derived_only_from_development_references() -> None:
    coverage = json.loads(
        (ROOT / "docs" / "results" / "llf-semantic-coverage-development.json").read_text(
            encoding="utf-8"
        )
    )
    vocabulary = coverage["vocabulary"]

    assert set(LLF_DEVELOPMENT_DIRECT_CALLS) == set(vocabulary["direct_call_names"])
    assert set(LLF_DEVELOPMENT_METHOD_ATTRIBUTES) == set(vocabulary["method_call_names"])
    assert set(LLF_DEVELOPMENT_BARE_SYMBOLS) == set(vocabulary["bare_symbol_names"])


def test_few_shot_examples_are_exact_development_records_and_trials_are_excluded() -> None:
    records = load_llf_records(LLF_DATA / "records.jsonl")
    by_id = {record.case_id: record for record in records}
    contract = llf_semantic_output_contract()

    for example in LLF_PROMPT_EXAMPLES:
        record = by_id[example.case_id]
        assert record.split == "development"
        assert record.trial_id == example.trial_id
        assert record.polarity == example.criterion_kind
        assert record.raw_text == example.source_text
        assert record.raw_text_sha256 == example.source_sha256
        assert record.logical_form == example.logical_form
        assert example.case_id not in contract.instructions
        assert example.source_sha256 not in contract.instructions
    assert len(LLF_PROMPT_EXAMPLES_SHA256) == 64

    development = load_llf_generation_split(LLF_DATA, "development")
    selected = select_development_canary(development.cases)
    assert LLF_PROMPT_EXAMPLE_TRIAL_IDS.isdisjoint(case.trial_id for case in selected)


def test_graph_contract_has_updated_frozen_schema_and_is_not_llf_quality_contract() -> None:
    contract = graph_v2_output_contract()

    assert contract.schema_sha256 == GRAPH_V2_SCHEMA_SHA256
    assert contract.track == "graph_v2_product"


def test_25_case_canary_is_deterministic_unique_trial_and_source_stratified() -> None:
    cases = _cases(200)

    first = select_development_canary(cases)
    second = select_development_canary(tuple(reversed(cases)))

    assert [case.case_id for case in first] == [case.case_id for case in second]
    assert len(first) == 25
    assert len({case.trial_id for case in first}) == 25
    assert {case.criterion_kind.value for case in first} == {"inclusion", "exclusion"}
    lengths = [len(case.source_text) for case in first]
    assert min(lengths) < 100 < max(lengths)


def test_llf_canary_cap_is_exact_and_graph_paid_lane_is_disabled(tmp_path: Path) -> None:
    cases = _cases(200)
    dataset = _dataset(cases, split="development")
    llf_plan, _ = build_llf_canary_plan(
        cases,
        dataset=dataset,
        contract=llf_semantic_output_contract(),
        created_at_utc=NOW,
        runtime_image_id=TEST_IMAGE_ID,
    )
    assert llf_plan.purpose == "development_llf_canary_25"
    assert llf_plan.runtime_image_id == TEST_IMAGE_ID
    assert llf_plan.reserved_total_usd == "0.163840000"
    assert llf_plan.budget_cap_usd == "0.170000000"
    with pytest.raises(ValueError, match="case-aware evidence validation"):
        build_graph_product_canary_plan(
            cases,
            dataset=dataset,
            contract=graph_v2_output_contract(),
            created_at_utc=NOW,
            runtime_image_id=TEST_IMAGE_ID,
        )
    with pytest.raises(ValueError, match="lossless LLF semantic"):
        build_locked_llf_plan(
            _cases(1_800),
            dataset=_dataset(
                _cases(1_800),
                split="test",
                dataset_id="leaf-logical-forms",
                dataset_version="llf-461288a",
            ),
            contract=graph_v2_output_contract(),
            created_at_utc=NOW,
            runtime_image_id=TEST_IMAGE_ID,
        )


def test_locked_llf_plan_reserves_under_exact_1180_cap() -> None:
    cases = _cases(1_800)
    plan = build_locked_llf_plan(
        cases,
        dataset=_dataset(
            cases,
            split="test",
            dataset_id="leaf-logical-forms",
            dataset_version="llf-461288a",
        ),
        contract=llf_semantic_output_contract(),
        created_at_utc=NOW,
        runtime_image_id=TEST_IMAGE_ID,
    )

    assert plan.reserved_total_usd == "11.796480000"
    assert Decimal(plan.budget_cap_usd) == LOCKED_BUDGET_CAP_USD
    assert plan.requires_separate_locked_authorization is True


def test_locked_authorization_is_structurally_disabled(
    tmp_path: Path,
) -> None:
    cases = _cases(1_800)
    plan = build_locked_llf_plan(
        cases,
        dataset=_dataset(
            cases,
            split="test",
            dataset_id="leaf-logical-forms",
            dataset_version="llf-461288a",
        ),
        contract=llf_semantic_output_contract(),
        created_at_utc=NOW,
        runtime_image_id=TEST_IMAGE_ID,
    )

    _, _, _, canary_binding, _ = _canary(tmp_path / "canary-binding-source")
    with pytest.raises(ValueError, match="structurally disabled"):
        run_directory = tmp_path / "locked"
        state_directory = _state_directory(run_directory)
        authorize_plan(
            plan,
            preregistration_path=_preregistration_path(tmp_path / "canary-binding-source"),
            execution_binding=canary_binding,
            authorization_id="locked-window-too-short",
            authorized_at_utc=NOW,
            run_directory=RUNTIME_OUTPUT_DIRECTORY,
            host_run_directory_sha256=run_directory_sha256(run_directory),
            authorization_state_directory_sha256=run_directory_sha256(state_directory),
            run_id="locked",
            acknowledgement=LOCKED_ACKNOWLEDGEMENT,
        )


def test_provider_request_is_exactly_source_text_and_polarity_with_fixed_controls() -> None:
    case = GenerationCase(
        case_id="SECRET-CASE-IDENTITY",
        trial_id="SECRET-TRIAL-IDENTITY",
        document_id="SECRET-PATH-IDENTITY",
        criterion_kind="exclusion",
        source_text="No prior chemotherapy.",
        source_sha256="f" * 64,
    )
    request = build_responses_request(case, llf_semantic_output_contract())
    provider_input_json = request["input"]
    assert isinstance(provider_input_json, str)
    provider_input = json.loads(provider_input_json)
    serialized = json.dumps(request, ensure_ascii=False, sort_keys=True)

    assert provider_input == {
        "criterion_kind": "exclusion",
        "criterion_text": "No prior chemotherapy.",
    }
    assert request["model"] == "gpt-5.6-luna"
    assert request["store"] is False
    assert request["reasoning"] == {"effort": "none"}
    assert request["max_output_tokens"] == 2_048
    assert request["service_tier"] == "default"
    assert request["tools"] == []
    text_config = request["text"]
    assert isinstance(text_config, dict)
    format_config = text_config["format"]
    assert isinstance(format_config, dict)
    assert format_config["strict"] is True
    assert (
        len(
            json.dumps(
                request,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        )
        <= 16_384
    )
    for forbidden in (
        case.case_id,
        case.trial_id,
        case.document_id,
        case.source_sha256,
        "reference_sha256",
        "source_path",
    ):
        assert forbidden not in serialized


def test_usage_prices_uncached_cached_cache_write_and_output_exactly() -> None:
    response = SimpleNamespace(
        usage=SimpleNamespace(
            input_tokens=1_000,
            output_tokens=50,
            input_tokens_details=SimpleNamespace(
                cached_tokens=200,
                cache_write_tokens=100,
            ),
        )
    )

    usage = usage_from_response(response)

    assert usage.uncached_input_tokens == 700
    assert usage.cached_input_tokens == 200
    assert usage.cache_write_input_tokens == 100
    assert usage.uncached_input_cost_usd == "0.000140000"
    assert usage.cached_input_cost_usd == "0.000004000"
    assert usage.cache_write_input_cost_usd == "0.000025000"
    assert usage.output_cost_usd == "0.000060000"
    assert usage.total_cost_usd == "0.000229000"


class FakeResponses:
    def __init__(self, response: object | None = None, error: Exception | None = None) -> None:
        self.response = response
        self.error = error
        self.requests: list[dict[str, Any]] = []

    async def create(self, **kwargs: Any) -> object:
        self.requests.append(kwargs)
        if self.error is not None:
            raise self.error
        assert self.response is not None
        return self.response


def _response(output_text: str, **changes: object) -> object:
    values: dict[str, object] = {
        "id": "resp_never_persist_raw",
        "model": "gpt-5.6-luna",
        "object": "response",
        "service_tier": "default",
        "status": "completed",
        "error": None,
        "incomplete_details": None,
        "output": [],
        "output_text": output_text,
        "usage": SimpleNamespace(
            input_tokens=100,
            output_tokens=20,
            input_tokens_details=SimpleNamespace(cached_tokens=0),
        ),
    }
    values.update(changes)
    return SimpleNamespace(**values)


async def test_transport_maps_failures_without_sdk_message_or_body() -> None:
    class PrivateError(Exception):
        status_code = 401

    secret = "sk-never-serialize-this raw-provider-body"
    responses = FakeResponses(error=PrivateError(secret))
    caller = LunaResponsesCaller(SimpleNamespace(responses=responses))

    result = await caller.call(_case(1), llf_semantic_output_contract())

    assert isinstance(result, StructuredCallFailure)
    assert result.failure.kind == "authentication"
    serialized = result.failure.model_dump_json()
    assert secret not in serialized
    assert "PrivateError" not in serialized
    assert len(responses.requests) == 1


async def test_transport_maps_429_to_fatal_rate_limit_without_private_body() -> None:
    class PrivateRateLimitError(Exception):
        status_code = 429
        code = "insufficient_quota"

    secret = "private-quota-response-body"
    responses = FakeResponses(error=PrivateRateLimitError(secret))
    caller = LunaResponsesCaller(SimpleNamespace(responses=responses))

    result = await caller.call(_case(1), llf_semantic_output_contract())

    assert isinstance(result, StructuredCallFailure)
    assert result.failure.kind == "rate_limit"
    assert result.failure.retryable is False
    assert secret not in result.failure.model_dump_json()
    assert len(responses.requests) == 1


@pytest.mark.parametrize("service_tier", (None, "flex", "priority"))
async def test_transport_rejects_missing_or_unexpected_returned_service_tier(
    service_tier: str | None,
) -> None:
    response = _response('{"logical_form":"cond(\\"x\\")"}', service_tier=service_tier)
    caller = LunaResponsesCaller(SimpleNamespace(responses=FakeResponses(response=response)))

    result = await caller.call(_case(1), llf_semantic_output_contract())

    assert isinstance(result, StructuredCallFailure)
    assert result.failure.kind == "response_contract"


@pytest.mark.parametrize("status", (None, "queued", "in_progress"))
async def test_transport_requires_exact_completed_response_status(
    status: str | None,
) -> None:
    response = _response('{"logical_form":"cond(\\"x\\")"}', status=status)
    caller = LunaResponsesCaller(SimpleNamespace(responses=FakeResponses(response=response)))

    result = await caller.call(_case(1), llf_semantic_output_contract())

    assert isinstance(result, StructuredCallFailure)
    assert result.failure.kind == "response_contract"


@pytest.mark.parametrize(
    ("response", "kind"),
    [
        (_response("{", status="completed"), "invalid_json"),
        (
            _response(
                "",
                status="incomplete",
                incomplete_details=SimpleNamespace(reason="max_output_tokens"),
            ),
            "truncated_output",
        ),
        (
            _response(
                "",
                output=[{"type": "message", "content": [{"type": "refusal"}]}],
            ),
            "refusal",
        ),
        (_response('{"logical_form":"eval(\\"x\\")"}'), "schema_validation"),
    ],
)
async def test_transport_maps_bounded_output_failures_and_continues(
    response: object,
    kind: str,
) -> None:
    caller = LunaResponsesCaller(SimpleNamespace(responses=FakeResponses(response=response)))

    result = await caller.call(_case(2), llf_semantic_output_contract())

    assert isinstance(result, StructuredCallFailure)
    assert result.failure.kind == kind


async def test_returned_model_alias_mismatch_is_exactly_recorded_and_fatal() -> None:
    response = _response(
        '{"logical_form":"cond(\\"x\\")"}',
        model="gpt-5.6-luna-alias",
    )
    caller = LunaResponsesCaller(SimpleNamespace(responses=FakeResponses(response=response)))

    result = await caller.call(_case(2), llf_semantic_output_contract())

    assert isinstance(result, StructuredCallFailure)
    assert result.failure.kind == "model_mismatch"
    assert result.provider_model == "gpt-5.6-luna-alias"
    assert result.provider_model_sha256 == hashlib.sha256(b"gpt-5.6-luna-alias").hexdigest()


def test_sdk_override_environment_is_rejected_before_client_construction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENAI_CUSTOM_HEADERS", '{"x-unsafe":"override"}')

    def forbidden_client(**kwargs: object) -> object:
        del kwargs
        raise AssertionError("SDK client must not be constructed with override environment")

    monkeypatch.setattr(live_transport, "AsyncOpenAI", forbidden_client)
    with pytest.raises(ValueError, match="override environment variables"):
        LunaResponsesCaller.from_api_key("not-a-real-key")


def test_execution_provenance_is_package_relative_and_wheel_lock_independent(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    implementation = frozen_execution_implementation()
    assert implementation.openai_sdk_version == "2.54.0"
    assert implementation.uv_lock_sha256 == live_contracts.EXPECTED_UV_LOCK_SHA256
    assert all(
        path.parent == live_contracts.MODULE_ROOT
        for path in live_contracts.EXECUTION_SOURCE_PATHS.values()
    )

    wheel_package_root = tmp_path / "site-packages" / "criteriabench"
    shutil.copytree(ROOT / "src" / "criteriabench", wheel_package_root)
    (tmp_path / "uv.lock").write_bytes((ROOT / "uv.lock").read_bytes())
    wheel_module_root = wheel_package_root / "real_live"
    monkeypatch.setattr(live_contracts, "MODULE_ROOT", wheel_module_root)
    assert frozen_execution_implementation() == implementation


def test_transitive_package_inventory_detects_parser_or_binding_tampering(
    tmp_path: Path,
) -> None:
    package_root = tmp_path / "criteriabench"
    shutil.copytree(ROOT / "src" / "criteriabench", package_root)
    before = live_contracts._package_python_inventory_sha256(package_root)
    parser_path = package_root / "real" / "llf_semantics.py"
    parser_path.write_bytes(parser_path.read_bytes() + b"\n# provenance-tamper-test\n")

    assert live_contracts._package_python_inventory_sha256(package_root) != before


def test_runtime_image_copies_exact_lockfile_to_audited_path() -> None:
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")

    assert "COPY --from=builder --chown=app:app /build/uv.lock /app/uv.lock" in dockerfile


def test_stdin_api_key_reader_accepts_exactly_one_bounded_line(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        sys,
        "stdin",
        SimpleNamespace(buffer=io.BytesIO(b"not-a-real-key\n")),
    )
    assert cli._read_api_key_stdin() == "not-a-real-key"

    monkeypatch.setattr(
        sys,
        "stdin",
        SimpleNamespace(buffer=io.BytesIO(b"first\nsecond\n")),
    )
    with pytest.raises(ValueError, match="trailing data"):
        cli._read_api_key_stdin()


def test_docker_wrapper_uses_stdin_key_and_mounts_only_source_generation() -> None:
    wrapper = (ROOT / "scripts" / "run-real-luna.ps1").read_text(encoding="utf-8")

    assert "--api-key-stdin" in wrapper
    assert "--env OPENAI_API_KEY" not in wrapper
    assert "ZeroFreeBSTR" in wrapper
    assert "docker image inspect" in wrapper
    assert "runtime_image_id" in wrapper
    assert "--runtime-image-id" in wrapper
    assert "--preregistration" in wrapper
    assert "--execution-binding" in wrapper
    assert "--authorization-state-directory-sha256" in wrapper
    assert "recover @sealedRunArguments" in wrapper
    assert "--network=none" in wrapper
    assert wrapper.index("recover @sealedRunArguments") < wrapper.index("Read-Host")
    assert "already terminal; no API key was requested" in wrapper
    assert "--read-only" in wrapper
    assert "--cap-drop=ALL" in wrapper
    assert "generation_manifest.json" in wrapper
    assert "generation_cases.jsonl" in wrapper
    assert "split_assignments.json" in wrapper
    for forbidden in (
        "records.jsonl",
        "development_references.jsonl",
        "test_references.jsonl",
    ):
        assert forbidden not in wrapper


def test_offline_plan_bind_and_authorization_are_separate_exact_image_phases() -> None:
    plan_wrapper = (ROOT / "scripts" / "plan-real-luna-canary.ps1").read_text(encoding="utf-8")
    bind_wrapper = (ROOT / "scripts" / "bind-real-luna-canary.ps1").read_text(encoding="utf-8")
    authorize_wrapper = (ROOT / "scripts" / "authorize-real-luna-canary.ps1").read_text(
        encoding="utf-8"
    )

    assert "plan-llf-canary" in plan_wrapper
    assert "authorize'" not in plan_wrapper
    assert "no authorization was created" in plan_wrapper
    assert "Plan SHA256" in plan_wrapper
    assert "Selected case-set SHA256" in plan_wrapper
    assert "Budget cap USD" in plan_wrapper
    assert "bind-execution" in bind_wrapper
    assert "authorize'" not in bind_wrapper
    assert "no authorization was created" in bind_wrapper
    assert "ReviewedPreregistrationSha256" in bind_wrapper
    assert "ReviewedPlanSha256" in bind_wrapper
    assert "--intended-run-id" in bind_wrapper
    assert "--intended-authorization-id" in bind_wrapper
    assert "--host-output-directory-sha256" in bind_wrapper
    assert "--authorization-state-directory-sha256" in bind_wrapper
    assert "authorize'" in authorize_wrapper
    assert "plan-llf-canary" not in authorize_wrapper
    for wrapper in (plan_wrapper, bind_wrapper, authorize_wrapper):
        assert "docker image inspect" in wrapper
        assert "--network=none" in wrapper
        assert "OPENAI_API_KEY" not in wrapper
    assert "--runtime-image-id" in plan_wrapper
    assert "--runtime-output-path', '/run/artifacts/output'" in authorize_wrapper
    for required_review in (
        "ReviewedPlanSha256",
        "ReviewedPreregistrationSha256",
        "ReviewedExecutionBindingSha256",
        "ReviewedCaseSetSha256",
        "ApprovedBudgetCapUsd",
        "CanaryAcknowledgement",
    ):
        assert f"[string]${required_review}" in authorize_wrapper
    assert "[string]$plan.plan_sha256 -cne $ReviewedPlanSha256" in authorize_wrapper
    assert (
        "[string]$preregistration.preregistration_sha256 -cne $ReviewedPreregistrationSha256"
        in authorize_wrapper
    )
    assert (
        "[string]$executionBinding.execution_binding_sha256 -cne $ReviewedExecutionBindingSha256"
        in authorize_wrapper
    )
    assert "[string]$plan.selected_case_set_sha256 -cne $ReviewedCaseSetSha256" in authorize_wrapper
    assert "[string]$plan.budget_cap_usd -cne $ApprovedBudgetCapUsd" in authorize_wrapper
    assert "[ValidatePattern('^0\\.170000000$')]" in authorize_wrapper
    assert LLF_CANARY_ACKNOWLEDGEMENT in authorize_wrapper
    assert "$plan.purpose -cne 'development_llf_canary_25'" in authorize_wrapper
    assert "generation_manifest.json" in plan_wrapper
    assert "generation_cases.jsonl" in plan_wrapper
    assert "split_assignments.json" in plan_wrapper


def test_runtime_image_precreates_every_fixed_read_only_mount_target() -> None:
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")

    for target in (
        "/run/artifacts",
        "/run/generation",
        "/run/authorization-state",
        "/run/bind",
        "/run/dataset",
        "/run/coverage",
        "/run/report",
    ):
        assert target in dockerfile


def test_decision_and_locked_plan_wrappers_preserve_exact_pass_chain() -> None:
    decision_wrapper = (ROOT / "scripts" / "decide-real-luna-canary.ps1").read_text(
        encoding="utf-8"
    )
    locked_wrapper = (ROOT / "scripts" / "plan-real-luna-locked.ps1").read_text(encoding="utf-8")

    for wrapper in (decision_wrapper, locked_wrapper):
        assert "docker image inspect" in wrapper
        assert "--network=none" in wrapper
        assert "OPENAI_API_KEY" not in wrapper
        assert "--read-only" in wrapper
        assert "--cap-drop=ALL" in wrapper
    assert "criteriabench-llf-canary-preregister" in decision_wrapper
    assert "decide @chainArguments" in decision_wrapper
    assert "check-decision @chainArguments" in decision_wrapper
    for flag in (
        "--preregistration",
        "--execution-binding",
        "--plan",
        "--authorization",
        "--score-report",
    ):
        assert flag in decision_wrapper

    assert "plan-locked" in locked_wrapper
    assert "authorize" not in locked_wrapper
    assert "no locked paid authorization was created" in locked_wrapper
    assert "advancement_status -cne 'pass'" in locked_wrapper
    assert "proceed_to_separate_locked_authorization -ne $true" in locked_wrapper
    for flag in (
        "--preregistration",
        "--execution-binding",
        "--canary-plan",
        "--canary-authorization",
        "--score-report",
        "--advancement-decision",
    ):
        assert flag in locked_wrapper
    assert "generation_manifest.json" in locked_wrapper
    assert "generation_cases.jsonl" in locked_wrapper
    assert "split_assignments.json" in locked_wrapper
    for forbidden in (
        "records.jsonl",
        "development_references.jsonl",
        "test_references.jsonl",
        "--network=bridge",
    ):
        assert forbidden not in locked_wrapper


def test_offline_score_wrapper_preserves_container_path_and_mount_boundary() -> None:
    wrapper = (ROOT / "scripts" / "score-real-luna-canary.ps1").read_text(encoding="utf-8")

    assert "docker image inspect" in wrapper
    assert "runtime_image_id" in wrapper
    assert "--network=none" in wrapper
    assert "--read-only" in wrapper
    assert "--cap-drop=ALL" in wrapper
    assert "dst=/run/artifacts/output,readonly" in wrapper
    assert "--run-dir', '/run/artifacts/output'" in wrapper
    assert "generation_manifest.json" in wrapper
    assert "generation_cases.jsonl" in wrapper
    assert "split_assignments.json" in wrapper
    assert "development_references.jsonl" in wrapper
    assert "dst=/run/dataset/development_references.jsonl,readonly" in wrapper
    assert "llf-semantic-coverage-development.json" in wrapper
    assert "dst=/run/report" in wrapper
    assert "'/opt/venv/bin/python'" in wrapper
    assert "criteriabench.real_eval.llf_live_score" in wrapper
    assert "Test-ContainedOrEqual -Path $reportResolved -Root $runResolved" in wrapper
    assert "Test-ContainedOrEqual -Path $runResolved -Root $reportResolved" in wrapper
    assert (
        "ReportOutputDirectory must be disjoint from sealed input and state directories" in wrapper
    )
    assert "^(?:attempt|case)-[0-9]{4}\\.json$" in wrapper
    for sealed_name in (
        "plan.json",
        "authorization.json",
        "authorization-consumed.json",
        "summary.json",
        "pending.json",
    ):
        assert sealed_name in wrapper
    for forbidden in (
        "records.jsonl",
        "test_references.jsonl",
        "llf-semantic-coverage-test.json",
        "--network=bridge",
    ):
        assert forbidden not in wrapper


def test_authorize_cli_parses_fixed_container_output_as_a_path() -> None:
    args = cli._parser().parse_args(
        [
            "authorize",
            "--artifact-root",
            str(ROOT),
            "--plan",
            "plan.json",
            "--preregistration",
            "preregistration.json",
            "--execution-binding",
            "execution-binding.json",
            "--output",
            "authorization.json",
            "--authorization-id",
            "auth-container-path-test",
            "--authorized-at-utc",
            NOW,
            "--run-id",
            "canary-01",
            "--runtime-output-path",
            "/run/artifacts/output",
            "--host-run-directory-sha256",
            "a" * 64,
            "--authorization-state-directory-sha256",
            "b" * 64,
            "--acknowledge-llf-canary",
        ]
    )

    assert isinstance(args.runtime_output_path, Path)
    assert args.runtime_output_path == Path("/run/artifacts/output")


def _locked_plan_cli_arguments(tmp_path: Path) -> argparse.Namespace:
    return cli._parser().parse_args(
        [
            "plan-locked",
            "--artifact-root",
            str(tmp_path),
            "--created-at-utc",
            NOW,
            "--runtime-image-id",
            TEST_IMAGE_ID,
            "--generation-root",
            str(LLF_DATA),
            "--output",
            "locked-plan.json",
            "--preregistration",
            "preregistration.json",
            "--execution-binding",
            "execution-binding.json",
            "--canary-plan",
            "canary-plan.json",
            "--canary-authorization",
            "canary-authorization.json",
            "--score-report",
            "canary-score.json",
            "--advancement-decision",
            "canary-decision.json",
        ]
    )


def _mock_locked_chain_loaders(
    monkeypatch: pytest.MonkeyPatch,
    *,
    plan: LivePlan,
    authorization: PaidAuthorization,
    binding: CanaryExecutionBinding,
    decision: SimpleNamespace,
) -> None:
    monkeypatch.setattr(canary_prereg, "load_preregistration", lambda _path: object())
    monkeypatch.setattr(canary_prereg, "load_execution_binding", lambda _path: binding)
    monkeypatch.setattr(canary_prereg, "load_live_plan", lambda _path: (plan, "1" * 64))
    monkeypatch.setattr(
        canary_prereg,
        "load_paid_authorization",
        lambda _path: (authorization, "2" * 64),
    )
    monkeypatch.setattr(
        canary_prereg,
        "load_live_score_report",
        lambda _path: (object(), "3" * 64),
    )
    monkeypatch.setattr(canary_prereg, "load_advancement_decision", lambda _path: decision)


def test_locked_plan_cli_requires_every_public_advancement_artifact(tmp_path: Path) -> None:
    with pytest.raises(SystemExit):
        cli._parser().parse_args(
            [
                "plan-locked",
                "--artifact-root",
                str(tmp_path),
                "--created-at-utc",
                NOW,
                "--runtime-image-id",
                TEST_IMAGE_ID,
                "--generation-root",
                str(LLF_DATA),
                "--output",
                "locked-plan.json",
            ]
        )


def test_locked_plan_cli_rejects_fail_or_tampered_advancement_before_test_loading(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    canary_plan, _, authorization, binding, _ = _canary(tmp_path / "locked-gate")
    args = _locked_plan_cli_arguments(tmp_path)
    failing = SimpleNamespace(
        advancement_status="fail",
        proceed_to_separate_locked_authorization=False,
    )
    _mock_locked_chain_loaders(
        monkeypatch,
        plan=canary_plan,
        authorization=authorization,
        binding=binding,
        decision=failing,
    )
    monkeypatch.setattr(
        canary_prereg,
        "verify_canary_advancement_decision",
        lambda *_args: None,
    )
    monkeypatch.setattr(
        cli,
        "load_llf_generation_split",
        lambda *_args: (_ for _ in ()).throw(
            AssertionError("FAIL decision must block before the locked split is opened")
        ),
    )

    with pytest.raises(ValueError, match="exact sealed PASS"):
        cli._create_plan(args)

    passing = SimpleNamespace(
        advancement_status="pass",
        proceed_to_separate_locked_authorization=True,
    )
    monkeypatch.setattr(canary_prereg, "load_advancement_decision", lambda _path: passing)
    monkeypatch.setattr(
        canary_prereg,
        "verify_canary_advancement_decision",
        lambda *_args: (_ for _ in ()).throw(ValueError("tampered advancement chain")),
    )
    with pytest.raises(ValueError, match="tampered advancement chain"):
        cli._create_plan(args)


def test_locked_plan_cli_builds_only_after_exact_pass_verifier(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    canary_plan, _, authorization, binding, _ = _canary(tmp_path / "locked-pass")
    args = _locked_plan_cli_arguments(tmp_path)
    passing = SimpleNamespace(
        advancement_status="pass",
        proceed_to_separate_locked_authorization=True,
    )
    _mock_locked_chain_loaders(
        monkeypatch,
        plan=canary_plan,
        authorization=authorization,
        binding=binding,
        decision=passing,
    )
    verified: list[object] = []
    monkeypatch.setattr(
        canary_prereg,
        "verify_canary_advancement_decision",
        lambda *_args: verified.append(_args),
    )

    locked = cli._create_plan(args)

    assert len(verified) == 1
    assert locked.purpose == "locked_llf_test"
    assert locked.runtime_image_id == canary_plan.runtime_image_id


@pytest.mark.parametrize("entrypoint", ("authorize", "recover", "run"))
@pytest.mark.parametrize(
    "attack",
    (
        "preregistration-hash",
        "advancement-gates-hash",
        "quality-failure-policy",
        "canonical-preregistration-gates",
    ),
)
async def test_direct_paid_core_rejects_forged_public_chain_without_side_effects(
    tmp_path: Path,
    entrypoint: str,
    attack: str,
) -> None:
    run_dir = tmp_path / f"core-{entrypoint}-{attack}"
    plan, cases, authorization, binding, contract = _canary(run_dir)
    state_dir = _state_directory(run_dir)
    caller = CallerInteractionProbe()
    preregistration_path = _preregistration_path(run_dir)
    attacked_binding = binding
    if attack == "canonical-preregistration-gates":
        preregistration_path = _forged_preregistration_path(run_dir)
    else:
        attacked_binding = _forged_execution_binding(binding, attack=attack)

    with pytest.raises(ValueError):
        if entrypoint == "authorize":
            authorize_plan(
                plan,
                preregistration_path=preregistration_path,
                execution_binding=attacked_binding,
                authorization_id=authorization.authorization_id,
                authorized_at_utc=NOW,
                run_directory=RUNTIME_OUTPUT_DIRECTORY,
                host_run_directory_sha256=run_directory_sha256(run_dir),
                authorization_state_directory_sha256=run_directory_sha256(state_dir),
                run_id=run_dir.name,
                acknowledgement=LLF_CANARY_ACKNOWLEDGEMENT,
            )
        elif entrypoint == "recover":
            recover_live_run(
                cases,
                plan=plan,
                authorization=authorization,
                execution_binding=attacked_binding,
                preregistration_path=preregistration_path,
                contract=contract,
                output_dir=run_dir,
                authorization_state_dir=state_dir,
                runtime_output_directory_sha256=RUNTIME_OUTPUT_DIRECTORY_SHA256,
                host_run_directory_sha256=run_directory_sha256(run_dir),
                authorization_state_directory_sha256=run_directory_sha256(state_dir),
                run_id=run_dir.name,
                runtime_image_id=TEST_IMAGE_ID,
                now=FIXED_NOW,
            )
        else:
            await run_live_plan(
                cases,
                plan=plan,
                authorization=authorization,
                execution_binding=attacked_binding,
                preregistration_path=preregistration_path,
                contract=contract,
                caller=caller,
                output_dir=run_dir,
                authorization_state_dir=state_dir,
                runtime_output_directory_sha256=RUNTIME_OUTPUT_DIRECTORY_SHA256,
                host_run_directory_sha256=run_directory_sha256(run_dir),
                authorization_state_directory_sha256=run_directory_sha256(state_dir),
                run_id=run_dir.name,
                runtime_image_id=TEST_IMAGE_ID,
                clock=_fixed_clock,
            )

    assert caller.calls == []
    assert caller.execution_identity_reads == 0
    _assert_no_paid_artifacts(run_dir, state_dir)


async def test_runner_is_sequential_sealed_and_exactly_derived(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    plan, cases, authorization, binding, contract = _canary(run_dir)
    caller = SuccessfulCaller()

    summary = await run_live_plan(
        cases,
        plan=plan,
        authorization=authorization,
        contract=contract,
        caller=caller,
        **_live_scope(run_dir, binding),
        clock=_fixed_clock,
    )

    assert caller.maximum_active == 1
    assert len(caller.calls) == 25
    assert summary.completed_count == 25
    assert summary.failed_count == 0
    assert summary.terminal_state == "completed"
    assert summary.attempted_count == 25
    assert summary.not_attempted_count == 0
    assert summary.observed_latency_case_count == 25
    assert Decimal(summary.charged_total_usd) <= Decimal(plan.budget_cap_usd)
    assert len(summary.outcome_hashes) == 25
    assert (run_dir / "authorization-consumed.json").is_file()
    outcomes = [
        CaseOutcome.model_validate_json((run_dir / f"case-{index:04d}.json").read_bytes())
        for index in range(1, 26)
    ]
    assert tuple(outcome.outcome_sha256 for outcome in outcomes) == summary.outcome_hashes
    assert all(outcome.response_id_sha256 is not None for outcome in outcomes)
    assert all(outcome.provider_model == "gpt-5.6-luna" for outcome in outcomes)
    assert all(outcome.provider_response_object == "response" for outcome in outcomes)
    charged = sum(
        (Decimal(outcome.charged_cost_usd) for outcome in outcomes),
        start=Decimal(0),
    )
    assert money(charged) == summary.charged_total_usd


@pytest.mark.parametrize(
    "kind",
    (
        "authentication",
        "authorization",
        "model_not_found",
        "rate_limit",
        "request_configuration",
    ),
)
async def test_fatal_provider_configuration_stops_after_exactly_one_paid_attempt(
    tmp_path: Path,
    kind: str,
) -> None:
    run_dir = tmp_path / f"fatal-{kind}"
    plan, cases, authorization, binding, contract = _canary(run_dir)
    caller = FatalCaller(kind)

    with pytest.raises(FatalProviderConfigurationError) as caught:
        await run_live_plan(
            cases,
            plan=plan,
            authorization=authorization,
            contract=contract,
            caller=caller,
            **_live_scope(run_dir, binding),
            clock=_fixed_clock,
        )

    summary = caught.value.summary
    assert len(caller.calls) == 1
    assert summary.terminal_state == "aborted"
    assert summary.abort_reason == kind
    assert summary.attempted_count == 1
    assert summary.not_attempted_count == 24
    assert (run_dir / "case-0001.json").is_file()
    assert not (run_dir / "case-0002.json").exists()

    with pytest.raises(FatalProviderConfigurationError):
        await run_live_plan(
            cases,
            plan=plan,
            authorization=authorization,
            contract=contract,
            caller=NeverCall(),
            **_live_scope(run_dir, binding),
            clock=_fixed_clock,
        )


async def test_escaped_caller_exception_is_fatal_after_one_attempt(tmp_path: Path) -> None:
    run_dir = tmp_path / "escaped-caller"
    plan, cases, authorization, binding, contract = _canary(run_dir)
    caller = EscapingCaller()

    with pytest.raises(FatalProviderConfigurationError) as caught:
        await run_live_plan(
            cases,
            plan=plan,
            authorization=authorization,
            contract=contract,
            caller=caller,
            **_live_scope(run_dir, binding),
            clock=_fixed_clock,
        )

    assert len(caller.calls) == 1
    assert caught.value.summary.abort_reason == "response_contract"
    outcome = CaseOutcome.model_validate_json((run_dir / "case-0001.json").read_bytes())
    assert outcome.failure is not None
    assert outcome.failure.kind == "response_contract"
    assert "private escaped" not in outcome.model_dump_json()


async def test_duplicate_provider_response_id_stops_before_third_call(
    tmp_path: Path,
) -> None:
    run_dir = tmp_path / "duplicate-response-id"
    plan, cases, authorization, binding, contract = _canary(run_dir)
    caller = DuplicateResponseIdCaller()

    with pytest.raises(FatalProviderConfigurationError) as caught:
        await run_live_plan(
            cases,
            plan=plan,
            authorization=authorization,
            contract=contract,
            caller=caller,
            **_live_scope(run_dir, binding),
            clock=_fixed_clock,
        )

    assert len(caller.calls) == 2
    assert caught.value.summary.attempted_count == 2
    duplicate = CaseOutcome.model_validate_json((run_dir / "case-0002.json").read_bytes())
    assert duplicate.failure is not None
    assert duplicate.failure.kind == "response_contract"
    assert not (run_dir / "attempt-0003.json").exists()


async def test_app_level_timeout_bounds_every_whole_call(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_dir = tmp_path / "whole-call-timeout"
    plan, cases, authorization, binding, contract = _canary(run_dir)
    caller = HangingCaller()
    monkeypatch.setattr(live_runner, "REQUEST_TIMEOUT_SECONDS", 0.01)

    summary = await asyncio.wait_for(
        run_live_plan(
            cases,
            plan=plan,
            authorization=authorization,
            contract=contract,
            caller=caller,
            **_live_scope(run_dir, binding),
            clock=_fixed_clock,
        ),
        timeout=30,
    )

    assert len(caller.calls) == 25
    assert summary.completed_count == 0
    assert summary.failed_count == 25
    assert all(
        CaseOutcome.model_validate_json(path.read_bytes()).failure.kind == "timeout"  # type: ignore[union-attr]
        for path in run_dir.glob("case-*.json")
    )


async def test_known_usage_over_reservation_is_preserved_then_aborts(tmp_path: Path) -> None:
    run_dir = tmp_path / "known-overage"
    plan, cases, authorization, binding, contract = _canary(run_dir)
    caller = HighUsageCaller()

    with pytest.raises(FatalProviderConfigurationError) as caught:
        await run_live_plan(
            cases,
            plan=plan,
            authorization=authorization,
            contract=contract,
            caller=caller,
            **_live_scope(run_dir, binding),
            clock=_fixed_clock,
        )

    outcome = CaseOutcome.model_validate_json((run_dir / "case-0001.json").read_bytes())
    assert len(caller.calls) == 1
    assert outcome.failure is not None and outcome.failure.kind == "budget_breach"
    assert outcome.usage.availability == "complete"
    assert Decimal(outcome.charged_cost_usd) > RESERVATION_PER_CASE_USD
    assert caught.value.summary.budget_breached is True
    assert caught.value.summary.charged_total_usd == outcome.charged_cost_usd


async def test_ancillary_conversion_failure_cannot_downgrade_known_usage(
    tmp_path: Path,
) -> None:
    run_dir = tmp_path / "malformed-ancillary"
    plan, cases, authorization, binding, contract = _canary(run_dir)
    caller = MalformedAncillaryCaller()

    with pytest.raises(FatalProviderConfigurationError):
        await run_live_plan(
            cases,
            plan=plan,
            authorization=authorization,
            contract=contract,
            caller=caller,
            **_live_scope(run_dir, binding),
            clock=_fixed_clock,
        )

    outcome = CaseOutcome.model_validate_json((run_dir / "case-0001.json").read_bytes())
    assert outcome.failure is not None and outcome.failure.kind == "response_contract"
    assert outcome.usage.availability == "complete"
    assert outcome.charged_cost_usd == _known_usage().total_cost_usd
    assert outcome.charged_cost_usd != money(RESERVATION_PER_CASE_USD)


async def test_resume_never_repeats_completed_calls(tmp_path: Path) -> None:
    run_dir = tmp_path / "resume"
    plan, cases, authorization, binding, contract = _canary(run_dir)
    first = await run_live_plan(
        cases,
        plan=plan,
        authorization=authorization,
        contract=contract,
        caller=SuccessfulCaller(),
        **_live_scope(run_dir, binding),
        clock=_fixed_clock,
    )

    second = await run_live_plan(
        cases,
        plan=plan,
        authorization=authorization,
        contract=contract,
        caller=NeverCall(),
        **_live_scope(run_dir, binding),
        clock=_fixed_clock,
    )

    assert second == first


async def test_existing_summary_with_deleted_outcome_fails_before_replay(
    tmp_path: Path,
) -> None:
    run_dir = tmp_path / "summary-replay-guard"
    plan, cases, authorization, binding, contract = _canary(run_dir)
    await run_live_plan(
        cases,
        plan=plan,
        authorization=authorization,
        contract=contract,
        caller=SuccessfulCaller(),
        **_live_scope(run_dir, binding),
        clock=_fixed_clock,
    )
    (run_dir / "case-0002.json").unlink()

    with pytest.raises(LiveRunError, match="summary does not exactly derive"):
        await run_live_plan(
            cases,
            plan=plan,
            authorization=authorization,
            contract=contract,
            caller=NeverCall(),
            **_live_scope(run_dir, binding),
            clock=_fixed_clock,
        )


async def test_swapped_case_artifact_filenames_fail_before_provider(
    tmp_path: Path,
) -> None:
    run_dir = tmp_path / "swapped-case-artifacts"
    plan, cases, authorization, binding, contract = _canary(run_dir)
    await run_live_plan(
        cases,
        plan=plan,
        authorization=authorization,
        contract=contract,
        caller=SuccessfulCaller(),
        **_live_scope(run_dir, binding),
        clock=_fixed_clock,
    )
    first_path = run_dir / "case-0001.json"
    second_path = run_dir / "case-0002.json"
    first_bytes = first_path.read_bytes()
    second_bytes = second_path.read_bytes()
    first_path.write_bytes(second_bytes)
    second_path.write_bytes(first_bytes)

    with pytest.raises(LiveRunError, match="filename differs from its sealed ordinal"):
        await run_live_plan(
            cases,
            plan=plan,
            authorization=authorization,
            contract=contract,
            caller=NeverCall(),
            **_live_scope(run_dir, binding),
            clock=_fixed_clock,
        )


async def test_swapped_attempt_artifacts_fail_before_provider(tmp_path: Path) -> None:
    run_dir = tmp_path / "swapped-attempt-artifacts"
    plan, cases, authorization, binding, contract = _canary(run_dir)
    await run_live_plan(
        cases,
        plan=plan,
        authorization=authorization,
        contract=contract,
        caller=SuccessfulCaller(),
        **_live_scope(run_dir, binding),
        clock=_fixed_clock,
    )
    first_path = run_dir / "attempt-0001.json"
    second_path = run_dir / "attempt-0002.json"
    first_bytes = first_path.read_bytes()
    second_bytes = second_path.read_bytes()
    first_path.write_bytes(second_bytes)
    second_path.write_bytes(first_bytes)

    with pytest.raises(LiveRunError, match="filename/ordinal is invalid"):
        RunArtifactStore(run_dir).load_attempts(plan, cases, contract)
    with pytest.raises(LiveRunError, match="existing sealed run artifact differs"):
        await run_live_plan(
            cases,
            plan=plan,
            authorization=authorization,
            contract=contract,
            caller=NeverCall(),
            **_live_scope(run_dir, binding),
            clock=_fixed_clock,
        )


async def test_resigned_wrong_request_hash_fails_before_provider(tmp_path: Path) -> None:
    run_dir = tmp_path / "wrong-request-hash"
    plan, cases, authorization, binding, contract = _canary(run_dir)
    with pytest.raises(FatalProviderConfigurationError):
        await run_live_plan(
            cases,
            plan=plan,
            authorization=authorization,
            contract=contract,
            caller=FatalCaller("authentication"),
            **_live_scope(run_dir, binding),
            clock=_fixed_clock,
        )
    (run_dir / "summary.json").unlink()
    _rewrite_pending_attempt(run_dir / "attempt-0001.json", request_sha256="0" * 64)

    with pytest.raises(LiveRunError, match="request hash differs"):
        RunArtifactStore(run_dir).load_attempts(plan, cases, contract)
    with pytest.raises(LiveRunError, match="existing sealed run artifact differs"):
        await run_live_plan(
            cases,
            plan=plan,
            authorization=authorization,
            contract=contract,
            caller=NeverCall(),
            **_live_scope(run_dir, binding),
            clock=_fixed_clock,
        )


async def test_resigned_outcome_timestamp_before_attempt_fails_before_provider(
    tmp_path: Path,
) -> None:
    run_dir = tmp_path / "backdated-outcome"
    plan, cases, authorization, binding, contract = _canary(run_dir)
    with pytest.raises(FatalProviderConfigurationError):
        await run_live_plan(
            cases,
            plan=plan,
            authorization=authorization,
            contract=contract,
            caller=FatalCaller("authentication"),
            **_live_scope(run_dir, binding),
            clock=_fixed_clock,
        )
    (run_dir / "summary.json").unlink()
    _rewrite_case_outcome(
        run_dir / "case-0001.json",
        outcome_finished_at_utc="2026-09-02T12:59:59Z",
    )

    with pytest.raises(LiveRunError, match="predates its paid attempt"):
        await run_live_plan(
            cases,
            plan=plan,
            authorization=authorization,
            contract=contract,
            caller=NeverCall(),
            **_live_scope(run_dir, binding),
            clock=_fixed_clock,
        )


async def test_fresh_outcome_is_chronology_checked_before_it_is_sealed(
    tmp_path: Path,
) -> None:
    run_dir = tmp_path / "backward-clock-during-call"
    plan, cases, authorization, binding, contract = _canary(run_dir)
    caller = SuccessfulCaller()
    moments = iter(
        (
            datetime(2026, 9, 2, 13, 0, 0, tzinfo=UTC),
            datetime(2026, 9, 2, 13, 0, 0, tzinfo=UTC),
            datetime(2026, 9, 2, 12, 59, 59, tzinfo=UTC),
        )
    )

    with pytest.raises(LiveRunError, match="predates its paid attempt"):
        await run_live_plan(
            cases,
            plan=plan,
            authorization=authorization,
            contract=contract,
            caller=caller,
            **_live_scope(run_dir, binding),
            clock=lambda: next(moments),
        )

    assert caller.calls == [cases[0].case_id]
    assert (run_dir / "attempt-0001.json").is_file()
    assert (run_dir / "pending.json").is_file()
    assert not (run_dir / "case-0001.json").exists()
    assert not (run_dir / "attempt-0002.json").exists()
    assert not (run_dir / "summary.json").exists()


async def test_external_attempt_ledger_prevents_full_local_rollback_replay(
    tmp_path: Path,
) -> None:
    run_dir = tmp_path / "full-rollback"
    plan, cases, authorization, binding, contract = _canary(run_dir)
    first_caller = SuccessfulCaller()
    await run_live_plan(
        cases,
        plan=plan,
        authorization=authorization,
        contract=contract,
        caller=first_caller,
        **_live_scope(run_dir, binding),
        clock=_fixed_clock,
    )
    assert len(first_caller.calls) == 25

    for path in (*run_dir.glob("attempt-*.json"), *run_dir.glob("case-*.json")):
        path.unlink()
    (run_dir / "summary.json").unlink()

    recovered = await run_live_plan(
        cases,
        plan=plan,
        authorization=authorization,
        contract=contract,
        caller=NeverCall(),
        **_live_scope(run_dir, binding),
        clock=_fixed_clock,
    )

    assert recovered.attempted_count == 25
    assert recovered.completed_count == 0
    assert recovered.failed_count == 25
    assert recovered.external_attempt_claim_count == 25
    assert all(
        CaseOutcome.model_validate_json(path.read_bytes()).failure is not None
        for path in run_dir.glob("case-*.json")
    )


async def test_external_attempt_ledger_reconstructs_partially_rolled_back_prefix(
    tmp_path: Path,
) -> None:
    run_dir = tmp_path / "partial-rollback"
    plan, cases, authorization, binding, contract = _canary(run_dir)
    with pytest.raises(FatalProviderConfigurationError):
        await run_live_plan(
            cases,
            plan=plan,
            authorization=authorization,
            contract=contract,
            caller=FatalCaller("authentication"),
            **_live_scope(run_dir, binding),
            clock=_fixed_clock,
        )
    (run_dir / "attempt-0001.json").unlink()
    (run_dir / "case-0001.json").unlink()
    (run_dir / "summary.json").unlink()

    scope = _live_scope(run_dir, binding)
    recovery = recover_live_run(
        cases,
        plan=plan,
        authorization=authorization,
        contract=contract,
        now=FIXED_NOW,
        **scope,
    )

    assert recovery.summary is None
    assert recovery.remaining_case_count == 24
    recreated = CaseOutcome.model_validate_json((run_dir / "case-0001.json").read_bytes())
    assert recreated.failure is not None
    assert recreated.failure.kind == "interrupted_unknown"


async def test_missing_external_attempt_claim_fails_closed_before_replay(
    tmp_path: Path,
) -> None:
    run_dir = tmp_path / "missing-external-attempt"
    plan, cases, authorization, binding, contract = _canary(run_dir)
    with pytest.raises(FatalProviderConfigurationError):
        await run_live_plan(
            cases,
            plan=plan,
            authorization=authorization,
            contract=contract,
            caller=FatalCaller("authentication"),
            **_live_scope(run_dir, binding),
            clock=_fixed_clock,
        )
    state_dir = _state_directory(run_dir)
    external_attempt = state_dir / f"attempt-{authorization.authorization_sha256}-0001.json"
    external_attempt.unlink()

    with pytest.raises(LiveRunError, match="local and external paid-attempt inventories"):
        recover_live_run(
            cases,
            plan=plan,
            authorization=authorization,
            contract=contract,
            now=FIXED_NOW,
            **_live_scope(run_dir, binding),
        )


async def test_fatal_outcome_without_summary_or_cleared_pending_never_advances(
    tmp_path: Path,
) -> None:
    run_dir = tmp_path / "fatal-crash-boundary"
    plan, cases, authorization, binding, contract = _canary(run_dir)
    with pytest.raises(FatalProviderConfigurationError):
        await run_live_plan(
            cases,
            plan=plan,
            authorization=authorization,
            contract=contract,
            caller=FatalCaller("authentication"),
            **_live_scope(run_dir, binding),
            clock=_fixed_clock,
        )
    (run_dir / "summary.json").unlink()
    shutil.copyfile(run_dir / "attempt-0001.json", run_dir / "pending.json")

    with pytest.raises(FatalProviderConfigurationError) as caught:
        await run_live_plan(
            cases,
            plan=plan,
            authorization=authorization,
            contract=contract,
            caller=NeverCall(),
            **_live_scope(run_dir, binding),
            clock=_fixed_clock,
        )
    assert caught.value.summary.attempted_count == 1
    assert caught.value.summary.not_attempted_count == 24
    assert not (run_dir / "pending.json").exists()
    assert not (run_dir / "attempt-0002.json").exists()


async def test_mismatched_caller_identity_fails_before_artifacts_or_provider(
    tmp_path: Path,
) -> None:
    class WrongIdentityCaller(NeverCall):
        @property
        def execution_identity_sha256(self) -> str:
            return "0" * 64

    run_dir = tmp_path / "wrong-caller"
    plan, cases, authorization, binding, contract = _canary(run_dir)

    with pytest.raises(LiveRunError, match="caller execution identity"):
        await run_live_plan(
            cases,
            plan=plan,
            authorization=authorization,
            contract=contract,
            caller=WrongIdentityCaller(),
            **_live_scope(run_dir, binding),
            clock=_fixed_clock,
        )
    assert not run_dir.exists()


async def test_pending_crash_is_charged_once_and_never_retried(tmp_path: Path) -> None:
    run_dir = tmp_path / "crash"
    plan, cases, authorization, binding, contract = _canary(run_dir)
    store = RunArtifactStore(run_dir)
    state_directory = _state_directory(run_dir)
    claim_store = AuthorizationClaimStore(state_directory)
    with store.lock():
        store.initialize(plan, authorization)
        claim = claim_store.claim_and_consume(plan, authorization, store)
        pending = _seal_pending(
            plan,
            1,
            cases[0],
            attempt_started_at_utc=NOW,
            request_digest=live_transport.request_sha256(cases[0], contract),
        )
        claim_store.claim_attempt(plan, authorization, claim, pending)
        store.write_pending(pending)
    caller = SuccessfulCaller()

    summary = await run_live_plan(
        cases,
        plan=plan,
        authorization=authorization,
        contract=contract,
        caller=caller,
        **_live_scope(run_dir, binding),
        clock=_fixed_clock,
    )

    first = CaseOutcome.model_validate_json((run_dir / "case-0001.json").read_bytes())
    assert first.failure is not None
    assert first.failure.kind == "interrupted_unknown"
    assert first.charged_cost_usd == money(RESERVATION_PER_CASE_USD)
    assert first.total_latency_ms is None
    assert len(caller.calls) == 24
    assert summary.failed_count == 1
    assert summary.usage_unknown_count == 1


def test_expired_pending_is_recovered_and_terminally_sealed_without_a_call(
    tmp_path: Path,
) -> None:
    run_dir = tmp_path / "expired-pending-recovery"
    plan, cases, authorization, binding, contract = _canary(run_dir)
    store = RunArtifactStore(run_dir)
    claim_store = AuthorizationClaimStore(_state_directory(run_dir))
    with store.lock():
        store.initialize(plan, authorization)
        claim = claim_store.claim_and_consume(plan, authorization, store)
        pending = _seal_pending(
            plan,
            1,
            cases[0],
            attempt_started_at_utc="2026-09-02T13:00:00Z",
            request_digest=live_transport.request_sha256(cases[0], contract),
        )
        claim_store.claim_attempt(plan, authorization, claim, pending)
        store.write_pending(pending)

    recovery = recover_live_run(
        cases,
        plan=plan,
        authorization=authorization,
        contract=contract,
        now=datetime(2026, 9, 2, 15, 0, 0, tzinfo=UTC),
        **_live_scope(run_dir, binding),
    )

    assert recovery.remaining_case_count == 0
    assert recovery.summary is not None
    assert recovery.summary.terminal_state == "aborted"
    assert recovery.summary.abort_reason == "interrupted_unknown"
    assert not (run_dir / "pending.json").exists()
    outcome = CaseOutcome.model_validate_json((run_dir / "case-0001.json").read_bytes())
    assert outcome.failure is not None
    assert outcome.failure.kind == "interrupted_unknown"


def test_recovery_refuses_to_seal_an_outcome_before_the_external_attempt(
    tmp_path: Path,
) -> None:
    run_dir = tmp_path / "backward-clock-recovery"
    plan, cases, authorization, binding, contract = _canary(run_dir)
    store = RunArtifactStore(run_dir)
    claim_store = AuthorizationClaimStore(_state_directory(run_dir))
    with store.lock():
        store.initialize(plan, authorization)
        claim = claim_store.claim_and_consume(plan, authorization, store)
        pending = _seal_pending(
            plan,
            1,
            cases[0],
            attempt_started_at_utc="2026-09-02T13:00:00Z",
            request_digest=live_transport.request_sha256(cases[0], contract),
        )
        claim_store.claim_attempt(plan, authorization, claim, pending)

    with pytest.raises(LiveRunError, match="recovery timestamp predates"):
        recover_live_run(
            cases,
            plan=plan,
            authorization=authorization,
            contract=contract,
            now=datetime(2026, 9, 2, 12, 59, 59, tzinfo=UTC),
            **_live_scope(run_dir, binding),
        )
    assert not (run_dir / "case-0001.json").exists()


async def test_completed_pending_pointer_conflicting_with_dangling_attempt_fails_closed(
    tmp_path: Path,
) -> None:
    run_dir = tmp_path / "conflicting-pending-pointer"
    plan, cases, authorization, binding, contract = _canary(run_dir)
    with pytest.raises(FatalProviderConfigurationError):
        await run_live_plan(
            cases,
            plan=plan,
            authorization=authorization,
            contract=contract,
            caller=FatalCaller("authentication"),
            **_live_scope(run_dir, binding),
            clock=_fixed_clock,
        )
    second = _seal_pending(
        plan,
        2,
        cases[1],
        attempt_started_at_utc=NOW,
        request_digest=live_transport.request_sha256(cases[1], contract),
    )
    store = RunArtifactStore(run_dir)
    store.write_pending(second)
    (run_dir / "pending.json").write_bytes((run_dir / "attempt-0001.json").read_bytes())

    with pytest.raises(
        LiveRunError,
        match="completed pending pointer conflicts with another dangling paid attempt",
    ):
        store.load_pending(plan, cases, contract)


def test_external_attempt_start_timestamps_must_be_monotonic(tmp_path: Path) -> None:
    run_dir = tmp_path / "backdated-external-attempt"
    plan, cases, authorization, binding, contract = _canary(run_dir)
    store = RunArtifactStore(run_dir)
    claim_store = AuthorizationClaimStore(_state_directory(run_dir))
    with store.lock():
        store.initialize(plan, authorization)
        claim = claim_store.claim_and_consume(plan, authorization, store)
        first = _seal_pending(
            plan,
            1,
            cases[0],
            attempt_started_at_utc="2026-09-02T13:00:00Z",
            request_digest=live_transport.request_sha256(cases[0], contract),
        )
        second = _seal_pending(
            plan,
            2,
            cases[1],
            attempt_started_at_utc="2026-09-02T12:59:59Z",
            request_digest=live_transport.request_sha256(cases[1], contract),
        )
        claim_store.claim_attempt(plan, authorization, claim, first)
        claim_store.claim_attempt(plan, authorization, claim, second)

    with pytest.raises(LiveRunError, match="timestamps are not monotonic"):
        recover_live_run(
            cases,
            plan=plan,
            authorization=authorization,
            contract=contract,
            now=FIXED_NOW,
            **_live_scope(run_dir, binding),
        )


async def test_authorization_cannot_be_replayed_in_another_directory(tmp_path: Path) -> None:
    authorized_dir = tmp_path / "authorized"
    plan, cases, authorization, binding, contract = _canary(authorized_dir)

    with pytest.raises(ValueError, match="different logical live-run ID"):
        await run_live_plan(
            cases,
            plan=plan,
            authorization=authorization,
            contract=contract,
            caller=NeverCall(),
            **_live_scope(tmp_path / "replay", binding),
            clock=_fixed_clock,
        )


async def test_expired_authorization_fails_before_artifacts_or_provider(
    tmp_path: Path,
) -> None:
    run_dir = tmp_path / "expired"
    plan, cases, authorization, binding, contract = _canary(run_dir)

    with pytest.raises(ValueError, match="authorization is not fresh"):
        await run_live_plan(
            cases,
            plan=plan,
            authorization=authorization,
            contract=contract,
            caller=NeverCall(),
            **_live_scope(run_dir, binding),
            clock=lambda: datetime(2026, 9, 2, 15, 0, 0, tzinfo=UTC),
        )
    assert not tuple(run_dir.glob("attempt-*.json"))
    assert not tuple(run_dir.glob("case-*.json"))
    assert not (run_dir / "authorization-consumed.json").exists()


async def test_freshness_is_rechecked_immediately_before_every_provider_call(
    tmp_path: Path,
) -> None:
    run_dir = tmp_path / "expires-between-calls"
    plan, cases, authorization, binding, contract = _canary(run_dir)
    caller = SuccessfulCaller()
    moments = iter(
        (
            datetime(2026, 9, 2, 13, 0, 0, tzinfo=UTC),
            datetime(2026, 9, 2, 13, 0, 0, tzinfo=UTC),
            datetime(2026, 9, 2, 13, 0, 0, tzinfo=UTC),
            datetime(2026, 9, 2, 14, 0, 1, tzinfo=UTC),
        )
    )

    with pytest.raises(ValueError, match="authorization is not fresh"):
        await run_live_plan(
            cases,
            plan=plan,
            authorization=authorization,
            contract=contract,
            caller=caller,
            **_live_scope(run_dir, binding),
            clock=lambda: next(moments),
        )

    assert len(caller.calls) == 1
    assert (run_dir / "case-0001.json").is_file()
    assert not (run_dir / "case-0002.json").exists()
    assert not (run_dir / "attempt-0002.json").exists()
    assert not (run_dir / "pending.json").exists()


async def test_failed_first_pre_call_guard_writes_no_paid_attempt(
    tmp_path: Path,
) -> None:
    run_dir = tmp_path / "expires-before-first-call"
    plan, cases, authorization, binding, contract = _canary(run_dir)
    caller = SuccessfulCaller()
    moments = iter(
        (
            datetime(2026, 9, 2, 13, 0, 0, tzinfo=UTC),
            datetime(2026, 9, 2, 14, 0, 1, tzinfo=UTC),
        )
    )

    with pytest.raises(ValueError, match="authorization is not fresh"):
        await run_live_plan(
            cases,
            plan=plan,
            authorization=authorization,
            contract=contract,
            caller=caller,
            **_live_scope(run_dir, binding),
            clock=lambda: next(moments),
        )

    assert caller.calls == []
    assert not tuple(run_dir.glob("attempt-*.json"))
    assert not tuple(run_dir.glob("case-*.json"))
    assert not (run_dir / "pending.json").exists()


async def test_resume_window_capacity_uses_only_the_exact_remaining_case_count(
    tmp_path: Path,
) -> None:
    run_dir = tmp_path / "one-case-resume-window"
    plan, cases, authorization, binding, contract = _canary(run_dir)
    first_caller = SuccessfulCaller()
    before_expiry = datetime(2026, 9, 2, 13, 0, 0, tzinfo=UTC)
    moments = iter([before_expiry] * (1 + 24 * 2) + [datetime(2026, 9, 2, 14, 0, 1, tzinfo=UTC)])

    with pytest.raises(ValueError, match="authorization is not fresh"):
        await run_live_plan(
            cases,
            plan=plan,
            authorization=authorization,
            contract=contract,
            caller=first_caller,
            **_live_scope(run_dir, binding),
            clock=lambda: next(moments),
        )
    assert len(first_caller.calls) == 24
    assert not (run_dir / "attempt-0025.json").exists()

    last_caller = SuccessfulCaller()
    resumed = await run_live_plan(
        cases,
        plan=plan,
        authorization=authorization,
        contract=contract,
        caller=last_caller,
        **_live_scope(run_dir, binding),
        clock=lambda: datetime(2026, 9, 2, 13, 58, 0, tzinfo=UTC),
    )

    assert last_caller.calls == [cases[24].case_id]
    assert resumed.completed_count == 25
    assert resumed.not_attempted_count == 0


def test_exclusive_store_lock_rejects_second_owner(tmp_path: Path) -> None:
    store = RunArtifactStore(tmp_path / "locked")
    with store.lock():
        with pytest.raises(LiveRunError, match="another process"):
            with store.lock():
                pass


def test_cli_default_run_path_reads_no_environment_and_creates_no_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def forbidden_key_read() -> str | None:
        raise AssertionError("default CLI path must not read OPENAI_API_KEY")

    monkeypatch.setattr(cli, "_process_openai_api_key", forbidden_key_read)
    with pytest.raises(ValueError, match="--live"):
        cli.main(
            [
                "run",
                "--artifact-root",
                str(ROOT),
                "--plan",
                "artifacts/real-live/missing-plan.json",
                "--authorization",
                "artifacts/real-live/missing-auth.json",
                "--preregistration",
                "artifacts/real-live/missing-preregistration.json",
                "--execution-binding",
                "artifacts/real-live/missing-execution-binding.json",
                "--generation-root",
                str(LLF_DATA),
                "--output-dir",
                "artifacts/real-live/missing-run",
                "--authorization-state-dir",
                str(ROOT),
                "--host-run-directory-sha256",
                "a" * 64,
                "--authorization-state-directory-sha256",
                "b" * 64,
                "--run-id",
                "missing-run",
                "--runtime-image-id",
                TEST_IMAGE_ID,
            ]
        )


def _cli_run_arguments(
    *,
    command: str,
    artifact_root: Path,
    run_dir: Path,
    state_dir: Path,
) -> argparse.Namespace:
    preregistration_path = artifact_root / "preregistration.json"
    preregistration_path.write_bytes(_preregistration_path(run_dir).read_bytes())
    values = [
        command,
        "--artifact-root",
        str(artifact_root),
        "--plan",
        "plan.json",
        "--authorization",
        "authorization.json",
        "--preregistration",
        "preregistration.json",
        "--execution-binding",
        "execution-binding.json",
        "--generation-root",
        str(LLF_DATA),
        "--output-dir",
        run_dir.name,
        "--authorization-state-dir",
        str(state_dir),
        "--host-run-directory-sha256",
        run_directory_sha256(run_dir),
        "--authorization-state-directory-sha256",
        run_directory_sha256(state_dir),
        "--run-id",
        run_dir.name,
        "--runtime-image-id",
        TEST_IMAGE_ID,
    ]
    if command == "run":
        values.extend(("--live", "--acknowledge-paid-api"))
    return cli._parser().parse_args(values)


def test_cli_recover_is_keyless_and_reports_exact_remaining_count(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    run_dir = tmp_path / "cli-recover"
    state_dir = _state_directory(run_dir)
    plan, cases, authorization, binding, _ = _canary(run_dir)
    args = _cli_run_arguments(
        command="recover",
        artifact_root=tmp_path,
        run_dir=run_dir,
        state_dir=state_dir,
    )

    monkeypatch.setattr(
        cli,
        "_load_verified_canary_chain",
        lambda **_kwargs: (plan, binding),
    )
    monkeypatch.setattr(cli, "_read_model", lambda _path, _model_type: authorization)
    monkeypatch.setattr(cli, "_cases_for_plan", lambda _plan, _root: cases)
    monkeypatch.setattr(cli, "run_directory_sha256", lambda _path: RUNTIME_OUTPUT_DIRECTORY_SHA256)
    monkeypatch.setattr(cli, "verify_execution_implementation", lambda _implementation: None)
    monkeypatch.setattr(
        live_runner, "verify_execution_implementation", lambda _implementation: None
    )
    monkeypatch.setattr(cli, "utc_now", _fixed_clock)
    monkeypatch.setattr(
        cli,
        "_process_openai_api_key",
        lambda: (_ for _ in ()).throw(AssertionError("recover must not read a key")),
    )
    monkeypatch.setattr(
        cli.LunaResponsesCaller,
        "from_api_key",
        staticmethod(
            lambda _key: (_ for _ in ()).throw(
                AssertionError("recover must not construct an OpenAI client")
            )
        ),
    )

    assert cli._recover(args) == 0
    assert "recovery_remaining=25" in capsys.readouterr().out


async def test_cli_run_direct_mock_e2e_wires_verified_paths_before_client(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_dir = tmp_path / "cli-run-e2e"
    state_dir = _state_directory(run_dir)
    plan, cases, authorization, binding, _ = _canary(run_dir)
    args = _cli_run_arguments(
        command="run",
        artifact_root=tmp_path,
        run_dir=run_dir,
        state_dir=state_dir,
    )
    received: dict[str, object] = {}

    class FakeClient:
        def __init__(self) -> None:
            self.closed = False

        async def aclose(self) -> None:
            self.closed = True

    fake_client = FakeClient()

    async def fake_run(cases_arg: object, **kwargs: object) -> SimpleNamespace:
        received["cases"] = cases_arg
        received.update(kwargs)
        return SimpleNamespace(
            summary_sha256="1" * 64,
            completed_count=25,
            failed_count=0,
            charged_total_usd="0.010000000",
        )

    monkeypatch.setattr(
        cli,
        "_load_verified_canary_chain",
        lambda **_kwargs: (plan, binding),
    )
    monkeypatch.setattr(cli, "_read_model", lambda _path, _model_type: authorization)
    monkeypatch.setattr(cli, "_cases_for_plan", lambda _plan, _root: cases)
    monkeypatch.setattr(cli, "run_directory_sha256", lambda _path: RUNTIME_OUTPUT_DIRECTORY_SHA256)
    monkeypatch.setattr(cli, "verify_execution_implementation", lambda _implementation: None)
    monkeypatch.setattr(
        live_runner, "verify_execution_implementation", lambda _implementation: None
    )
    monkeypatch.setattr(cli, "utc_now", _fixed_clock)
    monkeypatch.setattr(cli, "assert_clean_openai_environment", lambda _environment: None)
    monkeypatch.setattr(cli, "_process_openai_api_key", lambda: "test-only-key")
    monkeypatch.setattr(
        cli.LunaResponsesCaller,
        "from_api_key",
        staticmethod(lambda key: received.setdefault("api_key", key) and fake_client),
    )
    monkeypatch.setattr(cli, "run_live_plan", fake_run)

    assert await cli._run(args) == 0
    assert received["api_key"] == "test-only-key"
    assert received["cases"] == cases
    assert received["execution_binding"] == binding
    assert received["preregistration_path"] == (tmp_path / "preregistration.json").resolve()
    assert received["runtime_output_directory_sha256"] == RUNTIME_OUTPUT_DIRECTORY_SHA256
    assert received["host_run_directory_sha256"] == run_directory_sha256(run_dir)
    assert received["authorization_state_directory_sha256"] == run_directory_sha256(state_dir)
    assert received["output_dir"] == run_dir.resolve()
    assert received["authorization_state_dir"] == state_dir.resolve()
    assert fake_client.closed is True
    assert "test-only-key" not in "".join(
        path.read_text(encoding="utf-8") for path in run_dir.glob("*.json")
    )


async def test_cli_invalid_authorized_directory_touches_no_key_or_client(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    authorized_dir = tmp_path / "authorized"
    plan, cases, authorization, binding, _ = _canary(authorized_dir)
    arguments = cli._parser().parse_args(
        [
            "run",
            "--artifact-root",
            str(ROOT),
            "--plan",
            "artifacts/real-live/fake-plan.json",
            "--authorization",
            "artifacts/real-live/fake-authorization.json",
            "--preregistration",
            "artifacts/real-live/fake-preregistration.json",
            "--execution-binding",
            "artifacts/real-live/fake-execution-binding.json",
            "--generation-root",
            str(LLF_DATA),
            "--output-dir",
            "artifacts/real-live/different-run",
            "--authorization-state-dir",
            str(_state_directory(authorized_dir)),
            "--host-run-directory-sha256",
            run_directory_sha256(tmp_path / "different-run"),
            "--authorization-state-directory-sha256",
            run_directory_sha256(_state_directory(authorized_dir)),
            "--run-id",
            "different-run",
            "--runtime-image-id",
            TEST_IMAGE_ID,
            "--live",
            "--acknowledge-paid-api",
        ]
    )

    def fake_read(path: Path, model_type: type[BaseModel]) -> BaseModel:
        return plan if model_type is LivePlan else authorization

    def forbidden_key_read() -> str | None:
        raise AssertionError("invalid offline guards must prevent key access")

    monkeypatch.setattr(cli, "_read_model", fake_read)
    monkeypatch.setattr(
        cli,
        "_load_verified_canary_chain",
        lambda **_kwargs: (plan, binding),
    )
    monkeypatch.setattr(cli, "_cases_for_plan", lambda _plan, _root: cases)
    monkeypatch.setattr(cli, "verify_authorization", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        cli,
        "run_directory_sha256",
        lambda _path: RUNTIME_OUTPUT_DIRECTORY_SHA256,
    )
    monkeypatch.setattr(cli, "_process_openai_api_key", forbidden_key_read)

    with pytest.raises(ValueError, match="different logical live-run ID"):
        await cli._run(arguments)


async def test_cli_retargeted_image_id_fails_before_key_or_client(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_dir = tmp_path / "image-bound-run"
    plan, cases, authorization, binding, _ = _canary(run_dir)
    arguments = cli._parser().parse_args(
        [
            "run",
            "--artifact-root",
            str(tmp_path),
            "--plan",
            "fake-plan.json",
            "--authorization",
            "fake-authorization.json",
            "--preregistration",
            "fake-preregistration.json",
            "--execution-binding",
            "fake-execution-binding.json",
            "--generation-root",
            str(LLF_DATA),
            "--output-dir",
            run_dir.name,
            "--authorization-state-dir",
            str(_state_directory(run_dir)),
            "--host-run-directory-sha256",
            run_directory_sha256(run_dir),
            "--authorization-state-directory-sha256",
            run_directory_sha256(_state_directory(run_dir)),
            "--run-id",
            run_dir.name,
            "--runtime-image-id",
            "sha256:" + ("e" * 64),
            "--live",
            "--acknowledge-paid-api",
        ]
    )

    def fake_read(path: Path, model_type: type[BaseModel]) -> BaseModel:
        del path
        return plan if model_type is LivePlan else authorization

    def forbidden_key_read() -> str | None:
        raise AssertionError("image mismatch must prevent key access")

    monkeypatch.setattr(cli, "_read_model", fake_read)
    monkeypatch.setattr(
        cli,
        "_load_verified_canary_chain",
        lambda **_kwargs: (plan, binding),
    )
    monkeypatch.setattr(cli, "_cases_for_plan", lambda _plan, _root: cases)
    monkeypatch.setattr(cli, "verify_authorization", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        cli,
        "run_directory_sha256",
        lambda _path: RUNTIME_OUTPUT_DIRECTORY_SHA256,
    )
    monkeypatch.setattr(cli, "_process_openai_api_key", forbidden_key_read)

    with pytest.raises(ValueError, match="runtime image ID differs"):
        await cli._run(arguments)


def test_live_cli_plan_opens_source_only_generation_artifact_never_gold(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_read_bytes = Path.read_bytes
    opened: list[str] = []
    forbidden = {
        "manifest.json",
        "records.jsonl",
        "development_references.jsonl",
        "test_references.jsonl",
        "agreement_annotations.jsonl",
        "references.semantic.jsonl",
        "llf-semantic-coverage.json",
        "semantic_coverage.json",
    }

    def guarded_read_bytes(path: Path) -> bytes:
        opened.append(path.name)
        if path.name in forbidden:
            raise AssertionError("live planning attempted to open a gold/reference artifact")
        return original_read_bytes(path)

    monkeypatch.setattr(Path, "read_bytes", guarded_read_bytes)
    args = cli._parser().parse_args(
        [
            "plan-llf-canary",
            "--artifact-root",
            str(ROOT),
            "--created-at-utc",
            NOW,
            "--runtime-image-id",
            TEST_IMAGE_ID,
            "--generation-root",
            str(LLF_DATA),
            "--output",
            "artifacts/real-live/not-written.json",
        ]
    )

    plan = cli._create_plan(args)

    assert plan.purpose == "development_llf_canary_25"
    assert {
        "generation_manifest.json",
        "generation_cases.jsonl",
        "split_assignments.json",
    } <= set(opened)
    assert forbidden.isdisjoint(opened)


def test_live_cli_has_no_option_for_records_or_reference_paths() -> None:
    help_text = cli._parser().format_help()

    assert "--dataset" not in help_text
    assert "records.jsonl" not in help_text
    assert "reference" not in help_text


def test_live_plan_contains_only_generation_provenance_not_scorable_metadata(
    tmp_path: Path,
) -> None:
    plan, _, _, _, _ = _canary(tmp_path / "source-only-plan")
    serialized = plan.model_dump_json()

    assert "generation_manifest_sha256" in serialized
    assert "generation_cases_sha256" in serialized
    assert "split_assignments_sha256" in serialized
    assert "scorable" not in serialized
