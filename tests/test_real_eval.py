from __future__ import annotations

import shutil
from collections.abc import Mapping
from pathlib import Path
from typing import cast

import pytest
from pydantic import ValidationError

from criteriabench.real import (
    Comparator,
    Concept,
    CriterionKindV2,
    EligibilityGraphV2,
    EvidenceSpanV2,
    FlatAllOfNodeV2,
    FlatGraphOutputV2,
    FlatPredicateNodeV2,
    Modifier,
    ModifierKind,
    ScalarType,
    ScalarValue,
    SetValue,
    SourceDocument,
    Unit,
    canonical_graph_sha256,
    flat_graph_strict_json_schema,
    inflate_model_output,
)
from criteriabench.real_eval.bootstrap import (
    ClusterObservation,
    paired_trial_cluster_delta_interval,
    trial_cluster_interval,
)
from criteriabench.real_eval.generation import (
    FLAT_GRAPH_OUTPUT_SCHEMA_SHA256,
    BackendFailure,
    BackendOutcome,
    BackendSuccess,
    ProviderRequest,
    generate_bundle,
)
from criteriabench.real_eval.integrity import (
    canonical_sha256,
    case_set_sha256,
    render_bundle,
    seal_protocol,
    source_sha256,
    verify_bundle,
    verify_protocol,
)
from criteriabench.real_eval.llf_binding import (
    LLF_GENERATION_MANIFEST_SHA256,
    LlfBindingError,
    load_llf_generation_split,
)
from criteriabench.real_eval.metrics import (
    MatchCounts,
    compare_graphs,
    failed_graph_comparison,
)
from criteriabench.real_eval.models import (
    BUNDLE_SCHEMA_VERSION,
    PROTOCOL_SCHEMA_VERSION,
    CompletedPrediction,
    DatasetBinding,
    FailureDetail,
    FrozenProtocol,
    FrozenProtocolPayload,
    GenerationCase,
    InferenceParameters,
    PredictionBundle,
    PredictionBundlePayload,
    PredictionScoreReport,
    ReferenceCase,
    RunProvenance,
    TokenCounts,
    TokenPricing,
    UsageAccounting,
    UsagePricedCost,
    price_tokens,
    usd,
)
from criteriabench.real_eval.scoring import score_bundle

ROOT = Path(__file__).resolve().parents[1]
ZERO = "0.000000000"


class FakeBackend:
    name = "fake"
    model = "fake-model"

    def __init__(self, outcomes: Mapping[str, BackendOutcome | Exception | object]) -> None:
        self.outcomes = tuple(outcomes.values())
        self.seen: list[dict[str, object]] = []

    async def generate(self, request: ProviderRequest) -> BackendOutcome:
        payload = request.model_dump(mode="json")
        self.seen.append(payload)
        outcome = self.outcomes[(len(self.seen) - 1) % len(self.outcomes)]
        if isinstance(outcome, Exception):
            raise outcome
        return cast(BackendOutcome, outcome)


def _case(
    number: int,
    *,
    trial: int | None = None,
    text: str = "Age at least 18",
) -> GenerationCase:
    trial_number = number if trial is None else trial
    trial_id = f"NCT{trial_number:08d}"
    case_id = f"{trial_id}_{number}"
    return GenerationCase(
        case_id=case_id,
        trial_id=trial_id,
        document_id=f"leaf_logical_forms/annotator_1/batch1/{case_id}.js",
        criterion_kind=CriterionKindV2.INCLUSION,
        source_text=text,
        source_sha256=source_sha256(text),
    )


def _predicate_output(
    *,
    evidence: EvidenceSpanV2 | None = None,
) -> FlatGraphOutputV2:
    span = evidence or EvidenceSpanV2(start_char=0, end_char=3, quote="Age")
    return FlatGraphOutputV2(
        schema_version="2.0",
        root_node_id="age",
        nodes=(
            FlatPredicateNodeV2(
                node_id="age",
                kind="predicate",
                concept=Concept(text="Age"),
                comparator=Comparator.GREATER_THAN_OR_EQUAL,
                value=ScalarValue(kind="scalar", data_type=ScalarType.INTEGER, value=18),
                unit=Unit(text="years"),
                temporal=(),
                modifiers=(),
                evidence=(span,),
            ),
        ),
        review_required=False,
        review_reasons=(),
        not_machine_executable=False,
    )


def _root_none_output() -> FlatGraphOutputV2:
    return FlatGraphOutputV2(
        schema_version="2.0",
        root_node_id=None,
        nodes=(),
        review_required=True,
        review_reasons=("Cannot be represented without unsupported clinical inference",),
        not_machine_executable=True,
    )


def _inflate(case: GenerationCase, output: FlatGraphOutputV2) -> EligibilityGraphV2:
    return inflate_model_output(
        output,
        source=SourceDocument(
            trial_id=case.trial_id,
            document_id=case.document_id,
            text_sha256=case.source_sha256,
            text_length=len(case.source_text),
            source_url=None,
        ),
        criterion_id=case.case_id,
        criterion_kind=case.criterion_kind,
    )


def _reference(
    case: GenerationCase,
    output: FlatGraphOutputV2 | None,
    *,
    split: str = "test",
) -> ReferenceCase:
    graph = None if output is None else _inflate(case, output)
    return ReferenceCase(
        **case.model_dump(mode="python"),
        split=split,
        reference_status="missing_upstream" if graph is None else "available",
        reference_sha256=None if graph is None else canonical_graph_sha256(graph),
        reference=graph,
    )


def _pricing(
    input_rate: str = ZERO,
    output_rate: str = ZERO,
) -> TokenPricing:
    snapshot = {
        "currency": "USD",
        "input_usd_per_million_tokens": input_rate,
        "output_usd_per_million_tokens": output_rate,
        "pricing_id": "test-pricing",
        "rounding": "usd_9dp_half_up",
    }
    return TokenPricing(
        **snapshot,
        pricing_sha256=canonical_sha256(snapshot),
    )


def _usage(
    pricing: TokenPricing,
    *,
    input_tokens: int = 10,
    output_tokens: int = 5,
    total_attempts: int = 1,
    observed_attempts: int | None = None,
) -> UsageAccounting:
    observed = total_attempts if observed_attempts is None else observed_attempts
    if observed == 0:
        input_tokens = 0
        output_tokens = 0
    availability = (
        "unavailable" if observed == 0 else "complete" if observed == total_attempts else "partial"
    )
    input_cost = price_tokens(input_tokens, pricing.input_usd_per_million_tokens)
    output_cost = price_tokens(output_tokens, pricing.output_usd_per_million_tokens)
    return UsageAccounting(
        attempt_scope="all_attempts_including_retries",
        availability=availability,
        total_attempts=total_attempts,
        observed_attempts=observed,
        monetary_totals_are_lower_bounds=availability != "complete",
        tokens=TokenCounts(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            total_tokens=input_tokens + output_tokens,
        ),
        cost=UsagePricedCost(
            input_cost_usd=usd(input_cost),
            output_cost_usd=usd(output_cost),
            total_cost_usd=usd(input_cost + output_cost),
        ),
    )


def _dataset(cases: list[GenerationCase], *, scorable: int) -> DatasetBinding:
    return DatasetBinding(
        dataset_id="leaf-logical-forms",
        dataset_version="llf-test",
        split="test",
        split_unit="trial_id",
        manifest_sha256="a" * 64,
        case_set_sha256=case_set_sha256(cases),
        case_count=len(cases),
        scorable_case_count=scorable,
    )


def _protocol(dataset: DatasetBinding) -> FrozenProtocol:
    return seal_protocol(
        FrozenProtocolPayload(
            schema_version=PROTOCOL_SCHEMA_VERSION,
            protocol_id="real-v1-test",
            dataset=dataset,
            locked_test_reference_policy="offline_only_after_bundle_sealed",
            failure_policy="zero_primary_metrics",
            bootstrap_unit="trial_id",
            bootstrap_resamples=100,
            bootstrap_seed=7291,
        )
    )


def _run(
    protocol: FrozenProtocol,
    pricing: TokenPricing,
    *,
    paid: bool = False,
) -> RunProvenance:
    return RunProvenance(
        run_id="test-run",
        created_at_utc="2026-09-02T00:00:00Z",
        provider="fake",
        model="fake-model",
        deployment=None,
        prompt_sha256="b" * 64,
        output_schema_sha256=FLAT_GRAPH_OUTPUT_SCHEMA_SHA256,
        code_sha256="c" * 64,
        config_sha256="d" * 64,
        protocol_sha256=protocol.protocol_sha256,
        inference=InferenceParameters(
            temperature=None,
            top_p=None,
            max_output_tokens=2_000,
            seed=None,
            reasoning_effort="low",
            request_timeout_ms=30_000,
            maximum_attempts=3,
        ),
        pricing=pricing,
        paid_inference=paid,
        network_used=paid,
    )


@pytest.mark.asyncio
async def test_generation_and_scoring_keep_failures_and_missing_references() -> None:
    cases = [_case(1), _case(2), _case(3)]
    references = [
        _reference(cases[0], _predicate_output()),
        _reference(cases[1], _predicate_output()),
        _reference(cases[2], None),
    ]
    dataset = _dataset(cases, scorable=2)
    protocol = _protocol(dataset)
    pricing = _pricing()
    backend = FakeBackend(
        {
            cases[0].case_id: BackendSuccess(
                output=_predicate_output(),
                raw_response_sha256="1" * 64,
                usage=_usage(pricing),
            ),
            cases[1].case_id: BackendFailure(
                failure=FailureDetail(
                    kind="rate_limit",
                    retryable=True,
                    message_sha256="2" * 64,
                ),
                usage=_usage(pricing, total_attempts=2, observed_attempts=1),
            ),
            cases[2].case_id: BackendSuccess(
                output=_predicate_output(),
                raw_response_sha256="3" * 64,
                usage=_usage(pricing, observed_attempts=0),
            ),
        }
    )

    bundle = await generate_bundle(
        cases,
        dataset=dataset,
        protocol=protocol,
        run=_run(protocol, pricing),
        backend=backend,
    )
    verify_bundle(bundle, references)
    assert [case.status for case in bundle.cases] == ["completed", "failed", "completed"]
    assert (
        backend.seen == [{"criterion_kind": "inclusion", "criterion_text": "Age at least 18"}] * 3
    )
    assert render_bundle(bundle).endswith(b"\n")

    report = score_bundle(bundle, references, protocol)
    replay = score_bundle(bundle, references, protocol)
    assert report.model_dump(mode="json") == replay.model_dump(mode="json")
    assert report.operational_case_count == 3
    assert report.scorable_case_count == 2
    assert report.unscorable_reference_count == 1
    assert report.completed_cases == 2
    assert report.failed_cases == 1
    assert report.completion_rate == 0.666667
    assert report.failure_counts == {"rate_limit": 1}
    assert report.primary_all_scorable.ast_exact_match_accuracy == 0.5
    assert report.primary_all_scorable.semantic_graph.f1 == 0.666667
    assert report.completed_only_diagnostic is not None
    assert report.completed_only_diagnostic.semantic_graph.f1 == 1.0
    assert report.cases[2].status == "unscorable"
    assert report.cases[2].semantic_graph is None
    assert set(report.trial_cluster_intervals) == {
        "semantic_graph_f1",
        "ast_exact_match_accuracy",
    }
    assert report.usage.complete_case_count == 1
    assert report.usage.partial_case_count == 1
    assert report.usage.unavailable_case_count == 1
    assert report.usage.total_attempts == 4
    assert report.usage.total_retries == 1
    assert report.usage.monetary_totals_are_lower_bounds is True

    tampered = report.model_dump(mode="json")
    tampered["completed_cases"] = 3
    with pytest.raises(ValidationError, match="completion counts"):
        PredictionScoreReport.model_validate(tampered)
    wrong_contract = report.model_dump(mode="json")
    wrong_contract["scoring_contract_sha256"] = "0" * 64
    with pytest.raises(ValidationError, match="scoring contract hash"):
        PredictionScoreReport.model_validate(wrong_contract)
    wrong_aggregate = report.model_dump(mode="json")
    primary = cast(dict[str, object], wrong_aggregate["primary_all_scorable"])
    primary["ast_exact_match_count"] = 0
    primary["ast_exact_match_accuracy"] = 0.0
    intervals = cast(dict[str, object], wrong_aggregate["trial_cluster_intervals"])
    ast_interval = cast(dict[str, object], intervals["ast_exact_match_accuracy"])
    ast_interval["estimate"] = 0.0
    with pytest.raises(ValidationError, match="case-level scores"):
        PredictionScoreReport.model_validate(wrong_aggregate)


@pytest.mark.asyncio
async def test_generation_turns_every_bad_backend_result_into_a_frozen_failure() -> None:
    cases = [_case(11), _case(12), _case(13), _case(14)]
    pricing = _pricing()
    bad_evidence = EvidenceSpanV2(start_char=4, end_char=6, quote="18")
    backend = FakeBackend(
        {
            cases[0].case_id: RuntimeError("secret provider message must not be persisted"),
            cases[1].case_id: BackendSuccess(
                output=_predicate_output(evidence=bad_evidence),
                raw_response_sha256="4" * 64,
                usage=_usage(pricing),
            ),
            cases[2].case_id: object(),
            cases[3].case_id: BackendSuccess(
                output=cast(FlatGraphOutputV2, object()),
                raw_response_sha256="5" * 64,
                usage=_usage(pricing),
            ),
        }
    )
    dataset = _dataset(cases, scorable=4)
    protocol = _protocol(dataset)
    bundle = await generate_bundle(
        cases,
        dataset=dataset,
        protocol=protocol,
        run=_run(protocol, pricing),
        backend=backend,
    )

    assert [case.status for case in bundle.cases] == ["failed"] * 4
    assert [case.failure.kind for case in bundle.cases if case.status == "failed"] == [
        "provider_error",
        "evidence_validation",
        "provider_error",
        "provider_error",
    ]
    serialized = render_bundle(bundle)
    assert b"secret provider message" not in serialized


@pytest.mark.asyncio
async def test_generation_rejects_reference_objects_and_wrong_schema_binding() -> None:
    case = _case(21)
    reference = _reference(case, _predicate_output())
    dataset = _dataset([case], scorable=1)
    protocol = _protocol(dataset)
    pricing = _pricing()
    backend = FakeBackend(
        {
            case.case_id: BackendSuccess(
                output=_predicate_output(),
                raw_response_sha256="6" * 64,
                usage=_usage(pricing),
            )
        }
    )
    with pytest.raises(ValueError, match="source-only"):
        await generate_bundle(
            [reference],
            dataset=dataset,
            protocol=protocol,
            run=_run(protocol, pricing),
            backend=backend,
        )

    wrong_run = _run(protocol, pricing).model_copy(update={"output_schema_sha256": "0" * 64})
    with pytest.raises(ValueError, match="FlatGraphOutputV2"):
        await generate_bundle(
            [case],
            dataset=dataset,
            protocol=protocol,
            run=wrong_run,
            backend=backend,
        )

    other_dataset = dataset.model_copy(update={"manifest_sha256": "f" * 64})
    other_protocol = _protocol(other_dataset)
    backend.seen.clear()
    with pytest.raises(ValueError, match="dataset binding"):
        await generate_bundle(
            [case],
            dataset=dataset,
            protocol=other_protocol,
            run=_run(other_protocol, pricing),
            backend=backend,
        )
    assert backend.seen == []


def test_null_root_is_a_scored_semantic_outcome_not_an_empty_graph() -> None:
    case = _case(31)
    graph = _inflate(case, _root_none_output())
    comparison = compare_graphs(graph, graph)
    failed = failed_graph_comparison(graph)
    assert comparison.ast_exact_match is True
    assert comparison.nodes == MatchCounts(1, 0, 0)
    assert comparison.semantic_graph.f1 == 1.0
    assert failed.nodes == MatchCounts(0, 0, 1)
    assert failed.semantic_graph.f1 == 0.0


def test_commutative_graph_order_does_not_change_exact_or_component_scores() -> None:
    case = _case(32, text="Age and BMI")
    age = FlatPredicateNodeV2(
        node_id="age",
        kind="predicate",
        concept=Concept(text="Age"),
        comparator=Comparator.EXISTS,
        value=None,
        unit=None,
        temporal=(),
        modifiers=(),
        evidence=(EvidenceSpanV2(start_char=0, end_char=3, quote="Age"),),
    )
    bmi = FlatPredicateNodeV2(
        node_id="bmi",
        kind="predicate",
        concept=Concept(text="BMI"),
        comparator=Comparator.EXISTS,
        value=None,
        unit=None,
        temporal=(),
        modifiers=(),
        evidence=(EvidenceSpanV2(start_char=8, end_char=11, quote="BMI"),),
    )

    def output(children: tuple[str, str]) -> FlatGraphOutputV2:
        return FlatGraphOutputV2(
            schema_version="2.0",
            root_node_id="root",
            nodes=(
                FlatAllOfNodeV2(
                    node_id="root",
                    kind="all_of",
                    child_node_ids=children,
                    evidence=(),
                ),
                age,
                bmi,
            ),
            review_required=False,
            review_reasons=(),
            not_machine_executable=False,
        )

    left = _inflate(case, output(("age", "bmi")))
    right = _inflate(case, output(("bmi", "age")))
    comparison = compare_graphs(left, right)
    assert comparison.ast_exact_match is True
    assert comparison.semantic_graph.false_positive == 0
    assert comparison.semantic_graph.false_negative == 0
    assert comparison.semantic_graph.f1 == 1.0


def test_set_membership_order_does_not_change_exact_or_component_scores() -> None:
    case = _case(34, text="Group A or B")

    def set_value(values: tuple[str, str]) -> SetValue:
        return SetValue(
            kind="set",
            items=tuple(
                ScalarValue(
                    kind="scalar",
                    data_type=ScalarType.STRING,
                    value=value,
                )
                for value in values
            ),
        )

    def output(values: tuple[str, str]) -> FlatGraphOutputV2:
        return FlatGraphOutputV2(
            schema_version="2.0",
            root_node_id="group",
            nodes=(
                FlatPredicateNodeV2(
                    node_id="group",
                    kind="predicate",
                    concept=Concept(text="Group"),
                    comparator=Comparator.IN,
                    value=set_value(values),
                    unit=None,
                    temporal=(),
                    modifiers=(
                        Modifier(
                            kind=ModifierKind.OTHER,
                            name="allowed subgroups",
                            value=set_value(values),
                            unit=None,
                            evidence=(),
                        ),
                    ),
                    evidence=(EvidenceSpanV2(start_char=0, end_char=5, quote="Group"),),
                ),
            ),
            review_required=False,
            review_reasons=(),
            not_machine_executable=False,
        )

    comparison = compare_graphs(
        _inflate(case, output(("A", "B"))),
        _inflate(case, output(("B", "A"))),
    )
    assert comparison.ast_exact_match is True
    assert comparison.nodes.f1 == 1.0
    assert comparison.predicates.f1 == 1.0


@pytest.mark.asyncio
async def test_postprocessing_exception_is_a_counted_sanitized_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    case = _case(35)
    dataset = _dataset([case], scorable=1)
    protocol = _protocol(dataset)
    pricing = _pricing()
    backend = FakeBackend(
        {
            case.case_id: BackendSuccess(
                output=_predicate_output(),
                raw_response_sha256="e" * 64,
                usage=_usage(pricing),
            )
        }
    )

    def fail_hash(_graph: EligibilityGraphV2) -> str:
        raise RecursionError("must not escape or be serialized")

    monkeypatch.setattr(
        "criteriabench.real_eval.generation.canonical_graph_sha256",
        fail_hash,
    )
    bundle = await generate_bundle(
        [case],
        dataset=dataset,
        protocol=protocol,
        run=_run(protocol, pricing),
        backend=backend,
    )
    assert bundle.cases[0].status == "failed"
    assert bundle.cases[0].failure.kind == "schema_validation"
    assert b"must not escape" not in render_bundle(bundle)


def test_evidence_metric_is_separate_from_semantic_predicate_metric() -> None:
    case = _case(33, text="Age 18")
    expected = _inflate(case, _predicate_output())
    alternate = _inflate(
        case,
        _predicate_output(evidence=EvidenceSpanV2(start_char=4, end_char=6, quote="18")),
    )
    comparison = compare_graphs(alternate, expected)
    assert comparison.nodes.f1 == 1.0
    assert comparison.predicates.f1 == 1.0
    assert comparison.concept_evidence.f1 == 0.0
    assert comparison.ast_exact_match is False


def test_pricing_protocol_usage_and_bundle_tampering_are_rejected() -> None:
    assert FLAT_GRAPH_OUTPUT_SCHEMA_SHA256 == canonical_sha256(flat_graph_strict_json_schema())
    assert FLAT_GRAPH_OUTPUT_SCHEMA_SHA256 != canonical_sha256(
        FlatGraphOutputV2.model_json_schema()
    )
    with pytest.raises(ValidationError, match="pricing_sha256"):
        TokenPricing(
            currency="USD",
            pricing_id="bad",
            pricing_sha256="0" * 64,
            input_usd_per_million_tokens="0.200000000",
            output_usd_per_million_tokens="1.200000000",
            rounding="usd_9dp_half_up",
        )
    with pytest.raises(ValidationError, match="reference"):
        ReferenceCase(
            **_case(41).model_dump(mode="python"),
            split="test",
            reference_status="missing_upstream",
            reference_sha256="1" * 64,
            reference=None,
        )
    with pytest.raises(ValidationError, match="availability"):
        UsageAccounting(
            attempt_scope="all_attempts_including_retries",
            availability="complete",
            total_attempts=2,
            observed_attempts=1,
            monetary_totals_are_lower_bounds=False,
            tokens=TokenCounts(input_tokens=0, output_tokens=0, total_tokens=0),
            cost=UsagePricedCost(
                input_cost_usd=ZERO,
                output_cost_usd=ZERO,
                total_cost_usd=ZERO,
            ),
        )

    case = _case(42)
    dataset = _dataset([case], scorable=1)
    protocol = _protocol(dataset)
    broken_protocol = protocol.model_copy(update={"protocol_sha256": "0" * 64})
    with pytest.raises(ValueError, match="protocol hash mismatch"):
        verify_protocol(broken_protocol)


@pytest.mark.asyncio
async def test_bundle_seal_detects_graph_hash_cost_and_payload_tampering() -> None:
    case = _case(51)
    dataset = _dataset([case], scorable=1)
    protocol = _protocol(dataset)
    pricing = _pricing()
    backend = FakeBackend(
        {
            case.case_id: BackendSuccess(
                output=_predicate_output(),
                raw_response_sha256="7" * 64,
                usage=_usage(pricing),
            )
        }
    )
    bundle = await generate_bundle(
        [case],
        dataset=dataset,
        protocol=protocol,
        run=_run(protocol, pricing),
        backend=backend,
    )
    body = bundle.model_dump(mode="json", exclude={"bundle_sha256"})
    cast(dict[str, object], cast(list[object], body["cases"])[0])["graph_sha256"] = "0" * 64
    with pytest.raises(ValidationError, match="graph hash"):
        PredictionBundlePayload.model_validate(body)

    broken_bundle = bundle.model_copy(update={"bundle_sha256": "0" * 64})
    with pytest.raises(ValueError, match="bundle hash mismatch"):
        verify_bundle(broken_bundle, [case])
    broken_serialized = bundle.model_dump(mode="json")
    broken_serialized["bundle_sha256"] = "0" * 64
    with pytest.raises(ValidationError, match="bundle_sha256"):
        PredictionBundle.model_validate(broken_serialized)

    serialized = bundle.model_dump(mode="json")
    case_payload = cast(dict[str, object], cast(list[object], serialized["cases"])[0])
    usage = cast(dict[str, object], case_payload["usage"])
    cost = cast(dict[str, object], usage["cost"])
    cost["input_cost_usd"] = "0.000000001"
    cost["total_cost_usd"] = "0.000000001"
    serialized.pop("bundle_sha256")
    with pytest.raises(ValidationError, match="input cost"):
        PredictionBundlePayload.model_validate(serialized)


@pytest.mark.asyncio
async def test_nonzero_token_rates_are_recomputed_and_unpaid_cost_is_rejected() -> None:
    case = _case(52)
    dataset = _dataset([case], scorable=1)
    protocol = _protocol(dataset)
    pricing = _pricing("0.200000000", "1.200000000")
    usage = _usage(
        pricing,
        input_tokens=1_000_000,
        output_tokens=500_000,
    )
    backend = FakeBackend(
        {
            case.case_id: BackendSuccess(
                output=_predicate_output(),
                raw_response_sha256="a" * 64,
                usage=usage,
            )
        }
    )
    paid_bundle = await generate_bundle(
        [case],
        dataset=dataset,
        protocol=protocol,
        run=_run(protocol, pricing, paid=True),
        backend=backend,
    )
    assert paid_bundle.cases[0].usage.cost.input_cost_usd == "0.200000000"
    assert paid_bundle.cases[0].usage.cost.output_cost_usd == "0.600000000"
    assert paid_bundle.cases[0].usage.cost.total_cost_usd == "0.800000000"

    with pytest.raises(ValidationError, match="unpaid inference"):
        await generate_bundle(
            [case],
            dataset=dataset,
            protocol=protocol,
            run=_run(protocol, pricing, paid=False),
            backend=backend,
        )

    run_payload = _run(protocol, pricing, paid=True).model_dump(mode="json")
    run_payload["network_used"] = False
    with pytest.raises(ValidationError, match="paid inference"):
        RunProvenance.model_validate(run_payload)


def test_trial_cluster_bootstrap_is_deterministic_and_trial_grouped() -> None:
    observations = [
        ClusterObservation("a", "NCT1", True, MatchCounts(2, 0, 0)),
        ClusterObservation("b", "NCT1", False, MatchCounts(0, 1, 1)),
        ClusterObservation("c", "NCT2", True, MatchCounts(1, 0, 0)),
    ]
    first = trial_cluster_interval(
        observations,
        "semantic_graph_f1",
        resamples=200,
        seed=91,
    )
    second = trial_cluster_interval(
        observations,
        "semantic_graph_f1",
        resamples=200,
        seed=91,
    )
    assert first == second
    assert first.cluster_count == 2
    assert first.resampling_unit == "trial_id"
    paired = paired_trial_cluster_delta_interval(
        observations,
        [
            ClusterObservation("a", "NCT1", False, MatchCounts(1, 1, 1)),
            ClusterObservation("b", "NCT1", False, MatchCounts(0, 1, 1)),
            ClusterObservation("c", "NCT2", False, MatchCounts(0, 0, 1)),
        ],
        "semantic_graph_f1",
        resamples=200,
        seed=91,
    )
    assert paired.estimate > 0
    assert paired.cluster_count == 2
    with pytest.raises(ValueError, match="positive"):
        trial_cluster_interval(observations, "semantic_graph_f1", resamples=0, seed=1)
    with pytest.raises(ValueError, match="duplicate"):
        paired_trial_cluster_delta_interval(
            [observations[0], observations[0]],
            [observations[0]],
            "semantic_graph_f1",
            resamples=10,
            seed=1,
        )


def test_frozen_llf_loader_proves_exact_manifest_split_and_source_only_cases(
    tmp_path: Path,
) -> None:
    dataset_dir = ROOT / "data" / "real" / "llf"
    development = load_llf_generation_split(dataset_dir, "development")
    test = load_llf_generation_split(dataset_dir, "test")
    assert development.dataset.generation_manifest_sha256 == LLF_GENERATION_MANIFEST_SHA256
    assert development.dataset.case_count == 200
    assert test.dataset.case_count == 1_800
    assert "scorable_case_count" not in type(development.dataset).model_fields
    development_trials = {case.trial_id for case in development.cases}
    test_trials = {case.trial_id for case in test.cases}
    assert not (development_trials & test_trials)
    assert case_set_sha256(test.cases) == test.dataset.case_set_sha256
    assert set(test.cases[0].model_dump()) == {
        "case_id",
        "trial_id",
        "document_id",
        "criterion_kind",
        "source_text",
        "source_sha256",
    }

    altered = tmp_path / "llf"
    altered.mkdir()
    for name in (
        "generation_manifest.json",
        "generation_cases.jsonl",
        "split_assignments.json",
    ):
        shutil.copy2(dataset_dir / name, altered / name)
    records = (altered / "generation_cases.jsonl").read_bytes()
    (altered / "generation_cases.jsonl").write_bytes(records.replace(b"folate", b"FOLATE", 1))
    with pytest.raises(LlfBindingError, match="artifact hash mismatch"):
        load_llf_generation_split(altered, "test")


def test_completed_prediction_contract_rejects_claimed_hash_mismatch() -> None:
    case = _case(61)
    graph = _inflate(case, _predicate_output())
    pricing = _pricing()
    with pytest.raises(ValidationError, match="graph hash"):
        PredictionBundlePayload(
            schema_version=BUNDLE_SCHEMA_VERSION,
            dataset=_dataset([case], scorable=1),
            run=_run(_protocol(_dataset([case], scorable=1)), pricing),
            cases=[
                CompletedPrediction(
                    case_id=case.case_id,
                    trial_id=case.trial_id,
                    document_id=case.document_id,
                    source_sha256=case.source_sha256,
                    request_sha256="8" * 64,
                    total_latency_ms=1,
                    usage=_usage(pricing),
                    status="completed",
                    raw_response_sha256="9" * 64,
                    graph_sha256="0" * 64,
                    prediction=graph,
                )
            ],
        )
