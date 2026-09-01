from __future__ import annotations

import ast
import hashlib
import json
import os
from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from criteriabench.predictions import cli as prediction_cli
from criteriabench.predictions.cli import run
from criteriabench.predictions.integrity import (
    canonical_json_bytes,
    canonical_sha256,
    compute_suite_sha256,
    current_output_schema_sha256,
    load_verified_bundle,
    render_bundle,
    seal_bundle,
)
from criteriabench.predictions.models import (
    BUNDLE_SCHEMA_VERSION,
    CURRENT_OUTPUT_SCHEMA_ID,
    CompletedPrediction,
    DatasetBinding,
    FailedPrediction,
    FailureDetail,
    InferenceParameters,
    OutputSchemaBinding,
    PredictionBundle,
    PredictionBundlePayload,
    PredictionScoreReport,
    RunProvenance,
    TokenPricing,
    TokenUsage,
    UsagePricedCost,
    UsageRecord,
)
from criteriabench.predictions.scoring import score_verified_bundle
from criteriabench.suite.loader import load_suite
from criteriabench.suite.models import LoadedSuite

PROJECT_ROOT = Path(__file__).resolve().parents[1]
MANIFEST_PATH = PROJECT_ROOT / "data" / "synthetic_v0_1" / "manifest.json"
HEX = hashlib.sha256(b"fake prediction fixture").hexdigest()


def _pricing(
    input_rate: str = "0.000000000",
    output_rate: str = "0.000000000",
) -> TokenPricing:
    snapshot = {
        "currency": "USD",
        "input_usd_per_million_tokens": input_rate,
        "output_usd_per_million_tokens": output_rate,
        "pricing_id": "fake-pricing-v1",
        "rounding": "usd_9dp_half_up",
    }
    return TokenPricing(
        **snapshot,
        pricing_sha256=canonical_sha256(snapshot),
    )


def _observed_usage() -> UsageRecord:
    return UsageRecord(
        availability="observed",
        attempt_scope="all_attempts_including_retries",
        tokens=TokenUsage(input_tokens=1, output_tokens=2, total_tokens=3),
        cost=UsagePricedCost(
            input_cost_usd="0.000000000",
            output_cost_usd="0.000000000",
            total_cost_usd="0.000000000",
        ),
    )


def _unavailable_usage() -> UsageRecord:
    return UsageRecord(
        availability="unavailable",
        attempt_scope="all_attempts_including_retries",
        tokens=TokenUsage(input_tokens=0, output_tokens=0, total_tokens=0),
        cost=UsagePricedCost(
            input_cost_usd="0.000000000",
            output_cost_usd="0.000000000",
            total_cost_usd="0.000000000",
        ),
    )


def _payload(
    loaded: LoadedSuite,
    *,
    failed_indexes: frozenset[int] = frozenset(),
    unavailable_usage_indexes: frozenset[int] = frozenset(),
) -> PredictionBundlePayload:
    cases: list[CompletedPrediction | FailedPrediction] = []
    for index, loaded_case in enumerate(loaded.cases):
        common: dict[str, Any] = {
            "case_path": loaded_case.path,
            "case_sha256": loaded_case.sha256,
            "trial_id": loaded_case.fixture.trial.trial_id,
            "request_sha256": hashlib.sha256(
                loaded_case.fixture.trial.trial_id.encode("ascii")
            ).hexdigest(),
            "latency_ms": index + 1,
            "retries": 0,
            "usage": _unavailable_usage()
            if index in unavailable_usage_indexes
            else _observed_usage(),
        }
        if index in failed_indexes:
            cases.append(
                FailedPrediction(
                    **common,
                    status="failed",
                    failure=FailureDetail(
                        kind="timeout",
                        retryable=True,
                        message_sha256=HEX,
                    ),
                )
            )
            continue
        prediction = loaded_case.fixture.reference
        cases.append(
            CompletedPrediction(
                **common,
                status="completed",
                raw_response_sha256=canonical_sha256(prediction),
                prediction_sha256=canonical_sha256(prediction),
                prediction=prediction,
            )
        )

    return PredictionBundlePayload(
        schema_version=BUNDLE_SCHEMA_VERSION,
        dataset=DatasetBinding(
            dataset_version=loaded.manifest.dataset_version,
            fixture_contract="synthetic-v0.1-fixture-v1",
            manifest_name="manifest.json",
            manifest_sha256=loaded.manifest_sha256,
            suite_sha256=compute_suite_sha256(loaded),
            case_count=len(loaded.cases),
        ),
        run=RunProvenance(
            run_id="fake-model-run-v1",
            created_at_utc="2026-09-01T00:00:00Z",
            provider="fake-provider",
            model="fake-model-1",
            deployment=None,
            api_version=None,
            prompt_sha256=HEX,
            output_schema=OutputSchemaBinding(
                kind="criteriabench_current",
                schema_id=CURRENT_OUTPUT_SCHEMA_ID,
                schema_sha256=current_output_schema_sha256(),
            ),
            code_sha256=HEX,
            config_sha256=HEX,
            inference=InferenceParameters(
                temperature=0.0,
                top_p=1.0,
                max_output_tokens=2_000,
                seed=20_260_901,
                reasoning_effort=None,
                response_format="strict_json_schema",
                request_timeout_ms=30_000,
            ),
            pricing=_pricing(),
            paid_inference=False,
            network_used=False,
        ),
        cases=cases,
    )


def _bundle(
    loaded: LoadedSuite,
    *,
    failed_indexes: frozenset[int] = frozenset(),
    unavailable_usage_indexes: frozenset[int] = frozenset(),
) -> PredictionBundle:
    return seal_bundle(
        _payload(
            loaded,
            failed_indexes=failed_indexes,
            unavailable_usage_indexes=unavailable_usage_indexes,
        )
    )


def _write_bundle(path: Path, bundle: PredictionBundle) -> None:
    path.write_bytes(render_bundle(bundle))


def _body(bundle: PredictionBundle) -> dict[str, Any]:
    payload = bundle.model_dump(mode="json")
    del payload["bundle_sha256"]
    return payload


def _reseal(payload: dict[str, Any]) -> PredictionBundle:
    return seal_bundle(PredictionBundlePayload.model_validate(payload))


def test_canonical_bundle_round_trip_and_perfect_offline_score(tmp_path: Path) -> None:
    loaded = load_suite(MANIFEST_PATH)
    bundle = _bundle(loaded)
    bundle_path = tmp_path / "fake-predictions.json"
    _write_bundle(bundle_path, bundle)

    verified, verified_suite = load_verified_bundle(bundle_path, MANIFEST_PATH)
    report = score_verified_bundle(verified, verified_suite)

    assert verified == bundle
    assert render_bundle(verified) == render_bundle(bundle)
    assert verified.bundle_sha256 == canonical_sha256(_body(verified))
    assert report.completion_rate == 1.0
    assert report.schema_valid_rate == 1.0
    assert report.failed_cases == 0
    assert report.primary_all_cases.micro_criterion_text_f1 == 1.0
    assert report.primary_all_cases.mean_macro_field_accuracy == 1.0
    assert report.completed_only_diagnostic == report.primary_all_cases
    assert report.usage.attempt_scope == "all_attempts_including_retries"
    assert report.usage.observed_case_count == 80
    assert report.usage.unavailable_case_count == 0
    assert report.usage.completeness == 1.0
    assert report.usage.monetary_totals_are_lower_bounds is False
    assert report.usage.input_tokens == 80
    assert report.usage.output_tokens == 160
    assert report.usage.total_cost_usd == "0.000000000"


def test_primary_score_penalizes_failures_and_labels_completed_only() -> None:
    loaded = load_suite(MANIFEST_PATH)
    bundle = _bundle(loaded, failed_indexes=frozenset({0}))
    report = score_verified_bundle(bundle, loaded)

    assert report.completed_cases == 79
    assert report.failed_cases == 1
    assert report.completion_rate == 0.9875
    assert report.schema_valid_rate == 0.9875
    assert report.failure_counts == {"timeout": 1}
    first_reference = loaded.cases[0].fixture.reference
    first_reference_count = len(first_reference.inclusion_criteria) + len(
        first_reference.exclusion_criteria
    )
    assert report.taxonomy.raw_counts.missing_criterion == first_reference_count
    assert report.primary_all_cases.mean_criterion_text_f1 == 0.9875
    assert report.primary_all_cases.criterion_text_perfect_trial_rate == 0.9875
    assert report.primary_all_cases.micro_criterion_text_recall < 1.0
    assert report.completed_only_diagnostic is not None
    assert report.completed_only_diagnostic.mean_criterion_text_f1 == 1.0
    assert report.cases[0].status == "failed"
    assert report.cases[0].criterion_text_f1 == 0.0
    assert report.cases[0].token_f1 == 0.0
    assert report.cases[0].macro_field_accuracy == 0.0


def test_bundle_rejects_hash_path_count_and_order_tampering(tmp_path: Path) -> None:
    loaded = load_suite(MANIFEST_PATH)
    original = _bundle(loaded)

    manifest_payload = _body(original)
    manifest_payload["dataset"]["manifest_sha256"] = "0" * 64
    manifest_bundle = _reseal(manifest_payload)
    manifest_path = tmp_path / "manifest-hash.json"
    _write_bundle(manifest_path, manifest_bundle)
    with pytest.raises(ValueError, match="manifest hash mismatch"):
        load_verified_bundle(manifest_path, MANIFEST_PATH)

    suite_payload = _body(original)
    suite_payload["dataset"]["suite_sha256"] = "0" * 64
    suite_bundle = _reseal(suite_payload)
    suite_path = tmp_path / "suite-hash.json"
    _write_bundle(suite_path, suite_bundle)
    with pytest.raises(ValueError, match="suite hash mismatch"):
        load_verified_bundle(suite_path, MANIFEST_PATH)

    case_payload = _body(original)
    case_payload["cases"][0]["case_sha256"] = "0" * 64
    case_bundle = _reseal(case_payload)
    case_path = tmp_path / "case-hash.json"
    _write_bundle(case_path, case_bundle)
    with pytest.raises(ValueError, match="case hash mismatch"):
        load_verified_bundle(case_path, MANIFEST_PATH)

    order_payload = _body(original)
    order_payload["cases"][0], order_payload["cases"][1] = (
        order_payload["cases"][1],
        order_payload["cases"][0],
    )
    order_bundle = _reseal(order_payload)
    order_path = tmp_path / "case-order.json"
    _write_bundle(order_path, order_bundle)
    with pytest.raises(ValueError, match="out of order"):
        load_verified_bundle(order_path, MANIFEST_PATH)

    missing_payload = _body(original)
    missing_payload["cases"].pop()
    missing_payload["dataset"]["case_count"] = 79
    missing_bundle = _reseal(missing_payload)
    missing_path = tmp_path / "missing-case.json"
    _write_bundle(missing_path, missing_bundle)
    with pytest.raises(ValueError, match=r"case count mismatch|missing suite cases"):
        load_verified_bundle(missing_path, MANIFEST_PATH)

    duplicate_payload = _body(original)
    duplicate_payload["cases"].append(deepcopy(duplicate_payload["cases"][0]))
    duplicate_payload["dataset"]["case_count"] = 81
    with pytest.raises(ValidationError, match="duplicate case paths"):
        PredictionBundlePayload.model_validate(duplicate_payload)


def test_bundle_rejects_output_tampering_and_noncanonical_json(tmp_path: Path) -> None:
    loaded = load_suite(MANIFEST_PATH)
    original = _bundle(loaded)

    prediction_payload = _body(original)
    prediction_payload["cases"][0]["prediction_sha256"] = "0" * 64
    prediction_bundle = _reseal(prediction_payload)
    prediction_path = tmp_path / "prediction-hash.json"
    _write_bundle(prediction_path, prediction_bundle)
    with pytest.raises(ValueError, match="prediction hash mismatch"):
        load_verified_bundle(prediction_path, MANIFEST_PATH)

    trial_payload = _body(original)
    trial_payload["cases"][0]["prediction"]["trial_id"] = "WRONG-TRIAL"
    trial_payload["cases"][0]["prediction_sha256"] = canonical_sha256(
        trial_payload["cases"][0]["prediction"]
    )
    trial_bundle = _reseal(trial_payload)
    trial_path = tmp_path / "trial-id.json"
    _write_bundle(trial_path, trial_bundle)
    with pytest.raises(ValueError, match="wrong trial ID"):
        load_verified_bundle(trial_path, MANIFEST_PATH)

    evidence_payload = _body(original)
    criterion = evidence_payload["cases"][0]["prediction"]["inclusion_criteria"][0]
    criterion["source_text"] = "Z"
    criterion["normalized_text"] = "z"
    criterion["evidence"] = {"start_char": 0, "end_char": 1, "quote": "Z"}
    evidence_payload["cases"][0]["prediction_sha256"] = canonical_sha256(
        evidence_payload["cases"][0]["prediction"]
    )
    evidence_bundle = _reseal(evidence_payload)
    evidence_path = tmp_path / "evidence.json"
    _write_bundle(evidence_path, evidence_bundle)
    with pytest.raises(ValueError, match="not an exact source substring"):
        load_verified_bundle(evidence_path, MANIFEST_PATH)

    wrong_hash = original.model_copy(update={"bundle_sha256": "0" * 64})
    wrong_hash_path = tmp_path / "bundle-hash.json"
    wrong_hash_path.write_bytes(canonical_json_bytes(wrong_hash) + b"\n")
    with pytest.raises(ValueError, match="bundle hash mismatch"):
        load_verified_bundle(wrong_hash_path, MANIFEST_PATH)

    noncanonical_path = tmp_path / "noncanonical.json"
    noncanonical_path.write_bytes(b" " + render_bundle(original))
    with pytest.raises(ValueError, match="not canonical JSON"):
        load_verified_bundle(noncanonical_path, MANIFEST_PATH)


def test_strict_contract_rejects_unknown_fields_bad_types_and_cost_claims() -> None:
    loaded = load_suite(MANIFEST_PATH)
    original = _bundle(loaded)
    body = _body(original)

    body["untrusted"] = "field"
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        PredictionBundlePayload.model_validate(body)

    bad_retry = _body(original)
    bad_retry["cases"][0]["retries"] = "0"
    with pytest.raises(ValidationError):
        PredictionBundlePayload.model_validate(bad_retry)

    bad_pricing = _body(original)
    bad_pricing["run"]["pricing"]["pricing_sha256"] = "0" * 64
    with pytest.raises(ValidationError, match="canonical pricing snapshot"):
        PredictionBundlePayload.model_validate(bad_pricing)

    priced_claim = _body(original)
    priced_claim["run"]["pricing"] = _pricing("2.000000000", "3.000000000").model_dump(mode="json")
    for case in priced_claim["cases"]:
        case["usage"]["cost"] = {
            "input_cost_usd": "0.000002000",
            "output_cost_usd": "0.000006000",
            "total_cost_usd": "0.000008000",
        }
    with pytest.raises(ValidationError, match="unpaid inference"):
        PredictionBundlePayload.model_validate(priced_claim)

    priced_claim["run"]["paid_inference"] = True
    PredictionBundlePayload.model_validate(priced_claim)
    arithmetic_claim = deepcopy(priced_claim)
    arithmetic_claim["cases"][0]["usage"]["cost"]["input_cost_usd"] = "0.000002001"
    arithmetic_claim["cases"][0]["usage"]["cost"]["total_cost_usd"] = "0.000008001"
    with pytest.raises(ValidationError, match="observed input cost"):
        PredictionBundlePayload.model_validate(arithmetic_claim)

    bad_total = _body(original)
    bad_total["cases"][0]["usage"]["tokens"]["total_tokens"] = 4
    with pytest.raises(ValidationError, match="total_tokens"):
        PredictionBundlePayload.model_validate(bad_total)


def test_offline_cli_checks_sidecar_writes_canonical_report_and_never_overwrites(
    tmp_path: Path,
) -> None:
    loaded = load_suite(MANIFEST_PATH)
    bundle = _bundle(loaded, failed_indexes=frozenset({0}))
    bundle_path = tmp_path / "bundle.json"
    check_path = tmp_path / "bundle.sha256"
    output_path = tmp_path / "score.json"
    _write_bundle(bundle_path, bundle)
    check_path.write_bytes((bundle.bundle_sha256 + "\n").encode("ascii"))

    argv = [
        "--bundle",
        str(bundle_path),
        "--manifest",
        str(MANIFEST_PATH),
        "--check",
        str(check_path),
        "--output",
        str(output_path),
    ]
    assert run(argv) == 0
    report = PredictionScoreReport.model_validate_json(output_path.read_bytes())
    assert output_path.read_bytes() == canonical_json_bytes(report) + b"\n"
    assert report.failed_cases == 1

    original_output = output_path.read_bytes()
    assert run(argv) == 2
    assert output_path.read_bytes() == original_output

    wrong_check = tmp_path / "wrong.sha256"
    wrong_check.write_bytes(("0" * 64 + "\n").encode("ascii"))
    rejected_output = tmp_path / "rejected.json"
    wrong_argv = [*argv]
    wrong_argv[wrong_argv.index(str(check_path))] = str(wrong_check)
    wrong_argv[wrong_argv.index(str(output_path))] = str(rejected_output)
    assert run(wrong_argv) == 2
    assert not rejected_output.exists()


def test_prediction_replay_package_has_no_live_or_settings_imports() -> None:
    package = PROJECT_ROOT / "src" / "criteriabench" / "predictions"
    forbidden = {
        "criteriabench.benchmark_cli",
        "criteriabench.config",
        "criteriabench.providers",
        "httpx",
        "openai",
        "requests",
        "socket",
    }
    for path in package.glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        imports: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imports.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module is not None:
                imports.add(node.module)
        assert not {
            imported
            for imported in imports
            if any(
                imported == blocked or imported.startswith(f"{blocked}.") for blocked in forbidden
            )
        }, path


def test_raw_unknown_json_field_is_rejected_before_integrity_checks(tmp_path: Path) -> None:
    loaded = load_suite(MANIFEST_PATH)
    payload = json.loads(render_bundle(_bundle(loaded)))
    payload["unexpected"] = True
    path = tmp_path / "unknown-field.json"
    path.write_bytes(canonical_json_bytes(payload) + b"\n")
    with pytest.raises(ValueError, match="strict v1 contract"):
        load_verified_bundle(path, MANIFEST_PATH)


def test_cli_rejects_environment_paths_before_any_artifact_read(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def unexpected_read(_bundle: Path, _manifest: Path) -> None:
        raise AssertionError("artifact loader must not run for environment-style paths")

    monkeypatch.setattr(prediction_cli, "load_verified_bundle", unexpected_read)
    ordinary_bundle = tmp_path / "bundle.json"
    ordinary_manifest = tmp_path / "manifest.json"
    ordinary_output = tmp_path / "score.json"
    variants = (
        ("--bundle", tmp_path / ".env.bundle"),
        ("--manifest", tmp_path / ".ENV.local" / "manifest.json"),
        ("--check", tmp_path / ".env.check"),
        ("--output", tmp_path / ".env.report"),
    )
    for option, guarded_path in variants:
        argv = [
            "--bundle",
            str(ordinary_bundle),
            "--manifest",
            str(ordinary_manifest),
            "--output",
            str(ordinary_output),
        ]
        if option == "--check":
            argv.extend((option, str(guarded_path)))
        else:
            argv[argv.index(option) + 1] = str(guarded_path)
        assert prediction_cli.run(argv) == 2


def test_cli_path_guard_rejects_output_input_collision(tmp_path: Path) -> None:
    bundle = tmp_path / "bundle.json"
    with pytest.raises(ValueError, match="must not overwrite"):
        prediction_cli._validate_paths(
            bundle,
            tmp_path / "manifest.json",
            None,
            bundle,
        )


def test_atomic_writer_cleans_temporary_file_when_publish_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = tmp_path / "nested" / "score.json"

    def fail_link(_source: Path, _destination: Path) -> None:
        raise OSError("simulated publish failure")

    monkeypatch.setattr(os, "link", fail_link)
    with pytest.raises(OSError, match="simulated publish failure"):
        prediction_cli._write_new_atomic(output, b'{"score":1}\n')

    assert not output.exists()
    assert not list(output.parent.glob(".*.tmp"))


def test_atomic_writer_preserves_competing_destination(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = tmp_path / "nested" / "score.json"
    competitor = b'{"competitor":true}\n'
    real_link = os.link

    def competing_link(source: Path, destination: Path) -> None:
        destination.write_bytes(competitor)
        real_link(source, destination)

    monkeypatch.setattr(os, "link", competing_link)
    with pytest.raises(FileExistsError):
        prediction_cli._write_new_atomic(output, b'{"score":1}\n')

    assert output.read_bytes() == competitor
    assert not list(output.parent.glob(".*.tmp"))


def test_unavailable_usage_is_explicit_and_monetary_total_is_a_lower_bound() -> None:
    loaded = load_suite(MANIFEST_PATH)
    bundle = _bundle(loaded, unavailable_usage_indexes=frozenset({0}))
    report = score_verified_bundle(bundle, loaded)

    assert report.primary_all_cases.micro_criterion_text_f1 == 1.0
    assert report.usage.observed_case_count == 79
    assert report.usage.unavailable_case_count == 1
    assert report.usage.completeness == 0.9875
    assert report.usage.monetary_totals_are_lower_bounds is True
    assert report.usage.input_tokens == 79
    assert report.usage.output_tokens == 158
    assert report.usage.total_tokens == 237
    assert report.usage.total_cost_usd == "0.000000000"


def test_unavailable_usage_requires_zero_placeholders() -> None:
    loaded = load_suite(MANIFEST_PATH)
    payload = _body(_bundle(loaded))
    payload["cases"][0]["usage"]["availability"] = "unavailable"

    with pytest.raises(ValidationError, match="zero token and cost placeholders"):
        PredictionBundlePayload.model_validate(payload)


def test_current_schema_hash_is_verified_and_external_schema_is_explicit(
    tmp_path: Path,
) -> None:
    loaded = load_suite(MANIFEST_PATH)
    original = _bundle(loaded)

    bad_current = _body(original)
    bad_current["run"]["output_schema"]["schema_sha256"] = "0" * 64
    bad_path = tmp_path / "bad-current-schema.json"
    _write_bundle(bad_path, _reseal(bad_current))
    with pytest.raises(ValueError, match="current output schema hash mismatch"):
        load_verified_bundle(bad_path, MANIFEST_PATH)

    external = _body(original)
    external["run"]["output_schema"] = {
        "kind": "external",
        "schema_id": "vendor.external.eligibility.v1",
        "schema_sha256": HEX,
    }
    external_path = tmp_path / "external-schema.json"
    _write_bundle(external_path, _reseal(external))
    verified, _ = load_verified_bundle(external_path, MANIFEST_PATH)
    assert verified.run.output_schema.kind == "external"

    mismatched_kind = _body(original)
    mismatched_kind["run"]["output_schema"]["kind"] = "external"
    with pytest.raises(ValidationError, match="kind and schema_id disagree"):
        PredictionBundlePayload.model_validate(mismatched_kind)


def test_report_contract_rejects_cross_field_tampering() -> None:
    loaded = load_suite(MANIFEST_PATH)
    report = score_verified_bundle(_bundle(loaded), loaded)
    original = report.model_dump(mode="json")

    no_coverage = deepcopy(original)
    no_coverage["usage"]["observed_case_count"] = 0
    no_coverage["usage"]["unavailable_case_count"] = 0
    no_coverage["usage"]["completeness"] = 0.0
    with pytest.raises(ValidationError, match="cover at least one case"):
        PredictionScoreReport.model_validate(no_coverage)

    bad_completeness = deepcopy(original)
    bad_completeness["usage"]["completeness"] = 0.5
    with pytest.raises(ValidationError, match="usage completeness"):
        PredictionScoreReport.model_validate(bad_completeness)

    bad_lower_bound = deepcopy(original)
    bad_lower_bound["usage"]["monetary_totals_are_lower_bounds"] = True
    with pytest.raises(ValidationError, match="lower-bound flag"):
        PredictionScoreReport.model_validate(bad_lower_bound)

    bad_tokens = deepcopy(original)
    bad_tokens["usage"]["total_tokens"] += 1
    with pytest.raises(ValidationError, match="total_tokens arithmetic"):
        PredictionScoreReport.model_validate(bad_tokens)

    bad_cost = deepcopy(original)
    bad_cost["usage"]["total_cost_usd"] = "0.000000001"
    with pytest.raises(ValidationError, match="total_cost_usd arithmetic"):
        PredictionScoreReport.model_validate(bad_cost)

    bad_status_counts = deepcopy(original)
    bad_status_counts["completed_cases"] = 79
    bad_status_counts["failed_cases"] = 1
    with pytest.raises(ValidationError, match="completion counts"):
        PredictionScoreReport.model_validate(bad_status_counts)

    bad_primary_count = deepcopy(original)
    bad_primary_count["primary_all_cases"]["case_count"] = 79
    with pytest.raises(ValidationError, match="primary aggregate case_count"):
        PredictionScoreReport.model_validate(bad_primary_count)

    bad_usage_count = deepcopy(original)
    bad_usage_count["usage"]["observed_case_count"] = 79
    with pytest.raises(ValidationError, match="usage summary case counts"):
        PredictionScoreReport.model_validate(bad_usage_count)

    bad_rates = deepcopy(original)
    bad_rates["completion_rate"] = 0.5
    with pytest.raises(ValidationError, match="completion or schema-valid rate"):
        PredictionScoreReport.model_validate(bad_rates)

    bad_diagnostic = deepcopy(original)
    bad_diagnostic["completed_only_diagnostic"]["case_count"] = 79
    with pytest.raises(ValidationError, match="completed-only diagnostic"):
        PredictionScoreReport.model_validate(bad_diagnostic)
