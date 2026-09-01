"""Secret-safe, single-process benchmark command with reproducible artifacts."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import inspect
import json
import math
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from pydantic import Field, model_validator

import criteriabench.domain.schemas as domain_schemas_module
import criteriabench.evaluation.metrics as evaluation_metrics_module
import criteriabench.providers.factory as provider_factory_module
import criteriabench.providers.mock as mock_module
import criteriabench.providers.openai as openai_module
import criteriabench.services.extraction as extraction_service_module
from criteriabench import __version__
from criteriabench.config import Settings
from criteriabench.db.repositories import RunRepository
from criteriabench.db.session import Database
from criteriabench.domain.schemas import ClinicalTrialEligibility, StrictModel, TrialDocument
from criteriabench.evaluation.metrics import EvaluationReport, evaluate_extraction
from criteriabench.providers.factory import create_provider
from criteriabench.services.extraction import (
    BudgetExceeded,
    ExtractionService,
    LiveBudget,
    ProvenanceError,
)

ABSOLUTE_LIVE_BUDGET_CEILING_USD = 2.0
PROJECT_ROOT = Path(__file__).resolve().parents[2]
LIVE_DATA_ROOTS = (PROJECT_ROOT / "data" / "public", PROJECT_ROOT / "data" / "synthetic")


class BenchmarkFixture(StrictModel):
    fixture_version: str = Field(default="1.0", min_length=1, max_length=100)
    trial: TrialDocument
    reference: ClinicalTrialEligibility | None = None
    provenance: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def reference_matches_trial(self) -> BenchmarkFixture:
        if self.reference is not None and self.reference.trial_id != self.trial.trial_id:
            raise ValueError("reference trial_id must match the fixture trial_id")
        return self


class LoadedCase(StrictModel):
    fixture_version: str
    fixture_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    source_path: str
    provenance: dict[str, str]
    trial: TrialDocument
    reference: ClinicalTrialEligibility | None


def main() -> None:
    parser = _parser()
    args = parser.parse_args()
    artifact: dict[str, Any] | None = None
    try:
        validate_paths(args.inputs, args.output, allow_overwrite=args.overwrite)
        settings, budget = validate_mode(args)
        if settings.provider == "openai":
            _validate_live_output(args.output)
        artifact = asyncio.run(run(args.inputs, settings=settings, budget_usd=budget))
        write_artifact(args.output, artifact)
    except (OSError, ValueError, BudgetExceeded) as exc:
        parser.error(str(exc))
    if artifact is not None and artifact["status"] != "completed":
        raise SystemExit(1)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run a deterministic benchmark, or an explicitly authorized paid benchmark"
    )
    parser.add_argument("inputs", nargs="+", type=Path, help="public/synthetic fixture JSON")
    parser.add_argument("--output", type=Path, default=Path("artifacts/benchmark.json"))
    parser.add_argument("--live", action="store_true", help="enable the configured live provider")
    parser.add_argument("--acknowledge-paid-api", action="store_true")
    parser.add_argument("--budget-usd", type=float)
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="replace an existing JSON artifact after all safety checks pass",
    )
    return parser


def validate_paths(
    inputs: list[Path],
    output: Path,
    *,
    allow_overwrite: bool = False,
) -> None:
    """Reject secret-like paths and accidental or unacknowledged overwrite."""

    if not inputs:
        raise ValueError("at least one input fixture is required")
    for path in [*inputs, output]:
        if any(_is_env_like(part) for part in path.parts):
            raise ValueError("env-like paths are not accepted by the benchmark command")
    output_resolved = output.resolve(strict=False)
    if any(path.resolve(strict=False) == output_resolved for path in inputs):
        raise ValueError("output must not overwrite an input fixture")
    if output.suffix.casefold() != ".json":
        raise ValueError("benchmark output must use a .json suffix")
    if output.exists() and not allow_overwrite:
        raise ValueError("benchmark output already exists; pass --overwrite to replace it")
    missing = [str(path) for path in inputs if not path.is_file()]
    if missing:
        raise ValueError(f"input fixture does not exist: {missing[0]}")


def _validate_live_output(output: Path) -> None:
    """Keep paid-run evidence within the repository artifact boundary."""

    artifact_root = (PROJECT_ROOT / "artifacts").resolve(strict=False)
    resolved_output = output.resolve(strict=False)
    try:
        resolved_output.relative_to(artifact_root)
    except ValueError as exc:
        raise ValueError(
            "live benchmark output must be under the project artifacts directory"
        ) from exc


def _is_env_like(name: str) -> bool:
    lowered = name.casefold()
    return lowered == ".env" or lowered.startswith(".env.")


def validate_mode(args: argparse.Namespace) -> tuple[Settings, float]:
    """Validate live switches using only the normal process environment."""

    if not args.live:
        if args.acknowledge_paid_api:
            raise ValueError("--acknowledge-paid-api is valid only together with --live")
        if args.budget_usd is not None:
            raise ValueError("--budget-usd is valid only together with --live")
        settings = Settings(
            _env_file=None,
            LLM_PROVIDER="mock",
            ALLOW_PAID_CALLS=False,
        )
        return settings, 0.0

    settings = Settings(_env_file=None)
    if not args.acknowledge_paid_api:
        raise ValueError("live mode requires --acknowledge-paid-api")
    if settings.provider != "openai":
        raise ValueError("live mode requires LLM_PROVIDER=openai")
    if not settings.allow_paid_calls:
        raise ValueError("live mode requires ALLOW_PAID_CALLS=true")
    if not settings.key_is_configured:
        raise ValueError("live mode requires OPENAI_API_KEY in the process environment")
    if args.budget_usd is None:
        raise ValueError("live mode requires an explicit --budget-usd")
    budget = args.budget_usd
    if not math.isfinite(budget):
        raise ValueError("live budget must be finite")
    if budget <= 0:
        raise ValueError("live budget must be greater than zero")
    if budget > ABSOLUTE_LIVE_BUDGET_CEILING_USD:
        raise ValueError("live budget exceeds the CriteriaBench $2 safety ceiling")
    if budget > settings.live_run_budget_usd:
        raise ValueError("requested budget exceeds LIVE_RUN_BUDGET_USD")
    if settings.pricing_model != settings.openai_model:
        raise ValueError("PRICING_MODEL must match OPENAI_MODEL")
    if settings.input_cost_per_million_usd <= 0 or settings.output_cost_per_million_usd <= 0:
        raise ValueError("live token prices must be positive")
    return settings, budget


async def run(
    paths: list[Path],
    *,
    settings: Settings,
    budget_usd: float,
) -> dict[str, Any]:
    """Run sequentially under one preflight ledger and return a non-secret artifact."""

    expected_hashes: dict[Path, str] | None = None
    if settings.provider == "openai":
        expected_hashes = await _manifested_live_hashes(paths)
    cases = await _load_cases(paths, expected_hashes=expected_hashes)
    if len(cases) > settings.max_batch_size:
        raise ValueError("input count exceeds the configured batch maximum")

    database = Database("sqlite+aiosqlite:///:memory:")
    repository = RunRepository(database)
    provider = create_provider(settings)
    budget = LiveBudget(budget_usd)
    max_attempts = settings.openai_max_retries + 1 if provider.name == "openai" else 1
    service = ExtractionService(
        provider=provider,
        repository=repository,
        live_budget=budget,
        estimated_input_tokens=settings.estimated_input_tokens_per_request,
        max_output_tokens=settings.max_output_tokens,
        input_price=settings.input_cost_per_million_usd,
        output_price=settings.output_cost_per_million_usd,
        max_document_characters=settings.max_document_characters,
        max_attempts=max_attempts,
    )
    projected = sum(service.estimated_request_cost(case.trial) for case in cases)
    if projected > budget_usd:
        await database.close()
        raise BudgetExceeded("the complete batch would exceed the authorization guard")

    results: list[dict[str, Any]] = []
    reports: list[EvaluationReport] = []
    status = "completed"
    try:
        for case in cases:
            try:
                outcome = await service.execute(case.trial, persist=False)
                report = (
                    evaluate_extraction(outcome.result.extraction, case.reference)
                    if case.reference is not None
                    else None
                )
                if report is not None:
                    reports.append(report)
                results.append(
                    {
                        "status": "completed",
                        "trial_id": case.trial.trial_id,
                        "source_path": case.source_path,
                        "fixture_version": case.fixture_version,
                        "fixture_sha256": case.fixture_sha256,
                        "provenance": case.provenance,
                        "extraction": outcome.result.extraction.model_dump(mode="json"),
                        "evaluation": report.model_dump(mode="json") if report else None,
                        "latency_ms": outcome.result.latency_ms,
                        "input_tokens": outcome.result.usage.input_tokens,
                        "output_tokens": outcome.result.usage.output_tokens,
                        "usage_priced_cost_usd": outcome.result.estimated_cost_usd,
                    }
                )
            except Exception as exc:
                status = "partial_failure"
                failure: dict[str, Any] = {
                    "status": "failed",
                    "trial_id": case.trial.trial_id,
                    "source_path": case.source_path,
                    "fixture_version": case.fixture_version,
                    "fixture_sha256": case.fixture_sha256,
                    "provenance": case.provenance,
                    "error_type": type(exc).__name__,
                }
                if isinstance(exc, ProvenanceError):
                    failure["error_code"] = exc.code
                    failure["error_details"] = dict(exc.safe_details)
                results.append(failure)
                break
    finally:
        await database.close()

    usage_priced_cost = round(
        sum(float(item.get("usage_priced_cost_usd", 0.0)) for item in results),
        6,
    )
    return {
        "artifact_version": "1.0",
        "created_at": datetime.now(UTC).isoformat(),
        "status": status,
        "application_version": __version__,
        "schema_version": "1.0",
        "extraction_contract_sha256": _contract_hash(settings),
        "evaluation_contract_sha256": _evaluation_contract_hash(),
        "provider": provider.name,
        "model": provider.model,
        "paid": provider.name != "mock",
        "authorization_guard_usd": budget_usd,
        "projected_authorization_usd": round(projected, 6),
        "authorization_consumed_usd": round(budget.spent_usd, 6),
        "pricing_model": settings.pricing_model,
        "pricing_usd_per_million_tokens": {
            "input": settings.input_cost_per_million_usd,
            "output": settings.output_cost_per_million_usd,
        },
        "max_attempts_per_case": max_attempts,
        "guard_note": (
            "Process-local preflight authorization guard; not a provider billing hard cap."
        ),
        "evaluated_cases": len(reports),
        "mean_exact_match_f1": _mean(reports, "exact_match_f1"),
        "mean_token_f1": _mean(reports, "token_f1"),
        "mean_macro_field_accuracy": _mean(reports, "macro_field_accuracy"),
        "total_usage_priced_cost_usd": usage_priced_cost,
        "results": results,
    }


async def _load_cases(
    paths: list[Path],
    *,
    expected_hashes: dict[Path, str] | None = None,
) -> list[LoadedCase]:
    """Read each fixture once, then hash-check and parse those exact bytes."""

    cases: list[LoadedCase] = []
    for path in paths:
        expected_hash: str | None = None
        if expected_hashes is not None:
            resolved = path.resolve(strict=True)
            expected_hash = expected_hashes.get(resolved)
            if expected_hash is None:
                raise ValueError(
                    "live inputs must be hash-pinned fixtures under data/public or data/synthetic"
                )

        raw = await asyncio.to_thread(path.read_bytes)
        fixture_hash = hashlib.sha256(raw).hexdigest()
        if expected_hash is not None and fixture_hash != expected_hash:
            raise ValueError("live fixture hash does not match its manifest")

        payload = json.loads(raw)
        fixture_version: str
        provenance: dict[str, str]
        if isinstance(payload, dict) and "trial" in payload:
            fixture = BenchmarkFixture.model_validate(payload)
            trial = fixture.trial
            reference = fixture.reference
            fixture_version = fixture.fixture_version
            provenance = fixture.provenance
        else:
            trial = TrialDocument.model_validate(payload)
            reference = None
            fixture_version = "legacy-trial-document"
            provenance = {}
        cases.append(
            LoadedCase(
                fixture_version=fixture_version,
                fixture_sha256=fixture_hash,
                source_path=_safe_source_path(path),
                provenance=provenance,
                trial=trial,
                reference=reference,
            )
        )
    return cases


async def _verify_manifested_live_inputs(paths: list[Path]) -> None:
    """Compatibility boundary that verifies and parses each fixture from one read."""

    expected_hashes = await _manifested_live_hashes(paths)
    await _load_cases(paths, expected_hashes=expected_hashes)


async def _manifested_live_hashes(paths: list[Path]) -> dict[Path, str]:
    """Resolve manifest hashes without reading fixture contents."""

    expected: dict[Path, str] = {}
    for root in LIVE_DATA_ROOTS:
        manifest_path = root / "manifest.json"
        if not manifest_path.is_file():
            raise ValueError("live fixture manifest is missing")
        raw_manifest = await asyncio.to_thread(manifest_path.read_bytes)
        manifest = json.loads(raw_manifest)
        records = manifest.get("records") if isinstance(manifest, dict) else None
        if not isinstance(records, list):
            raise ValueError("live fixture manifest has an invalid records list")
        resolved_root = root.resolve(strict=True)
        for record in records:
            if not isinstance(record, dict):
                raise ValueError("live fixture manifest contains an invalid record")
            relative = record.get("path")
            digest = record.get("sha256")
            if not isinstance(relative, str) or not isinstance(digest, str):
                raise ValueError("live fixture manifest record is incomplete")
            fixture_path = (root / relative).resolve(strict=True)
            try:
                fixture_path.relative_to(resolved_root)
            except ValueError as exc:
                raise ValueError("live fixture manifest path escapes its dataset") from exc
            expected[fixture_path] = digest

    selected: dict[Path, str] = {}
    for path in paths:
        resolved = path.resolve(strict=True)
        expected_hash = expected.get(resolved)
        if expected_hash is None:
            raise ValueError(
                "live inputs must be hash-pinned fixtures under data/public or data/synthetic"
            )
        selected[resolved] = expected_hash
    return selected


def _safe_source_path(path: Path) -> str:
    """Use a repository-relative label, never an absolute workstation path."""

    resolved = path.resolve(strict=False)
    try:
        return resolved.relative_to(PROJECT_ROOT).as_posix()
    except ValueError:
        return path.name


def _canonical_source(source: str) -> str:
    """Normalize source text so identical code hashes equally on every OS."""

    return source.replace("\r\n", "\n").replace("\r", "\n")


def _module_source(module: Any) -> str:
    return _canonical_source(inspect.getsource(module))


def _contract_hash(settings: Settings) -> str:
    """Bind extraction evidence to provider, orchestration, schema, and settings."""

    provider_module = openai_module if settings.provider == "openai" else mock_module
    canonical = json.dumps(
        {
            "application_version": __version__,
            "provider": settings.provider,
            "model": (
                settings.openai_model if settings.provider == "openai" else "deterministic-rules-v1"
            ),
            "pricing_model": settings.pricing_model,
            "input_price": settings.input_cost_per_million_usd,
            "output_price": settings.output_cost_per_million_usd,
            "max_output_tokens": settings.max_output_tokens,
            "max_retries": settings.openai_max_retries,
            "reasoning_effort": "none",
            "provider_module": _module_source(provider_module),
            "provider_factory_module": _module_source(provider_factory_module),
            "extraction_service_module": _module_source(extraction_service_module),
            "domain_schema_module": _module_source(domain_schemas_module),
            "schema": ClinicalTrialEligibility.model_json_schema(),
        },
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _evaluation_contract_hash() -> str:
    """Bind scores to the exact evaluator, schema, and provenance-validation code."""

    canonical = json.dumps(
        {
            "evaluation_module": _module_source(evaluation_metrics_module),
            "extraction_service_module": _module_source(extraction_service_module),
            "domain_schema_module": _module_source(domain_schemas_module),
            "report_schema": EvaluationReport.model_json_schema(),
        },
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _mean(reports: list[EvaluationReport], field: str) -> float | None:
    if not reports:
        return None
    return round(sum(float(getattr(report, field)) for report in reports) / len(reports), 6)


def write_artifact(output: Path, artifact: dict[str, Any]) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_name(f".{output.name}.{uuid4().hex}.tmp")
    try:
        temporary.write_text(
            json.dumps(artifact, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        temporary.replace(output)
    finally:
        temporary.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
