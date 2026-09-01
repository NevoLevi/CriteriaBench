"""Fixed-input, secret-safe entrypoint for the production Container Apps Job."""

from __future__ import annotations

import argparse
import asyncio
import base64
import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import Any

import criteriabench.benchmark_cli as benchmark_cli
from criteriabench.config import Settings

_BUDGET_USD = 0.02
_FIXTURE_NAME = "benchmark_case_001.json"
_FIXTURE_SHA256 = "879a1f5785752e4086020b9569c679643b27e002dff3100cb3638d8ecceda779"
_FIXTURE_B64 = "ewogICJmaXh0dXJlX3ZlcnNpb24iOiAiMS4wIiwKICAidHJpYWwiOiB7CiAgICAidHJpYWxfaWQiOiAiU1lOVEhFVElDLUJFTkNILTAwMSIsCiAgICAidGl0bGUiOiAiU3ludGhldGljIGJlbmNobWFyayBjYXNlIHdpdGggZ29sZCBsYWJlbHMiLAogICAgImVsaWdpYmlsaXR5X3RleHQiOiAiSW5jbHVzaW9uIENyaXRlcmlhOlxuLSBBZ2UgPj0gMTggeWVhcnNcblxuRXhjbHVzaW9uIENyaXRlcmlhOlxuLSBQcmVnbmFuY3kiLAogICAgInNvdXJjZV91cmwiOiBudWxsCiAgfSwKICAicmVmZXJlbmNlIjogewogICAgInNjaGVtYV92ZXJzaW9uIjogIjEuMCIsCiAgICAidHJpYWxfaWQiOiAiU1lOVEhFVElDLUJFTkNILTAwMSIsCiAgICAiaW5jbHVzaW9uX2NyaXRlcmlhIjogWwogICAgICB7CiAgICAgICAgImNyaXRlcmlvbl9pZCI6ICJJMDAxIiwKICAgICAgICAia2luZCI6ICJpbmNsdXNpb24iLAogICAgICAgICJjYXRlZ29yeSI6ICJhZ2UiLAogICAgICAgICJzb3VyY2VfdGV4dCI6ICJBZ2UgPj0gMTggeWVhcnMiLAogICAgICAgICJub3JtYWxpemVkX3RleHQiOiAiYWdlID49IDE4IHllYXJzIiwKICAgICAgICAiY29uY2VwdCI6ICJhZ2UiLAogICAgICAgICJvcGVyYXRvciI6ICJncmVhdGVyX3RoYW5fb3JfZXF1YWwiLAogICAgICAgICJ2YWx1ZSI6IDE4LAogICAgICAgICJ1bml0IjogInllYXJzIiwKICAgICAgICAibmVnYXRlZCI6IGZhbHNlLAogICAgICAgICJ0ZW1wb3JhbF9jb25zdHJhaW50IjogewogICAgICAgICAgInJlbGF0aW9uIjogInVuc3BlY2lmaWVkIiwKICAgICAgICAgICJxdWFudGl0eSI6IG51bGwsCiAgICAgICAgICAidW5pdCI6IG51bGwsCiAgICAgICAgICAicmVmZXJlbmNlX2V2ZW50IjogbnVsbCwKICAgICAgICAgICJyYXdfdGV4dCI6ICIiCiAgICAgICAgfSwKICAgICAgICAibG9naWNfZ3JvdXAiOiB7CiAgICAgICAgICAiZ3JvdXBfaWQiOiAiSUcwMDEiLAogICAgICAgICAgImNvbm5lY3RvciI6ICJzaW5nbGUiLAogICAgICAgICAgInBhcmVudF9ncm91cF9pZCI6IG51bGwKICAgICAgICB9LAogICAgICAgICJldmlkZW5jZSI6IHsKICAgICAgICAgICJzdGFydF9jaGFyIjogMjIsCiAgICAgICAgICAiZW5kX2NoYXIiOiAzNywKICAgICAgICAgICJxdW90ZSI6ICJBZ2UgPj0gMTggeWVhcnMiCiAgICAgICAgfQogICAgICB9CiAgICBdLAogICAgImV4Y2x1c2lvbl9jcml0ZXJpYSI6IFsKICAgICAgewogICAgICAgICJjcml0ZXJpb25faWQiOiAiRTAwMSIsCiAgICAgICAgImtpbmQiOiAiZXhjbHVzaW9uIiwKICAgICAgICAiY2F0ZWdvcnkiOiAicmVwcm9kdWN0aXZlIiwKICAgICAgICAic291cmNlX3RleHQiOiAiUHJlZ25hbmN5IiwKICAgICAgICAibm9ybWFsaXplZF90ZXh0IjogInByZWduYW5jeSIsCiAgICAgICAgImNvbmNlcHQiOiAicHJlZ25hbmN5IiwKICAgICAgICAib3BlcmF0b3IiOiAiZXhpc3RzIiwKICAgICAgICAidmFsdWUiOiB0cnVlLAogICAgICAgICJ1bml0IjogbnVsbCwKICAgICAgICAibmVnYXRlZCI6IGZhbHNlLAogICAgICAgICJ0ZW1wb3JhbF9jb25zdHJhaW50IjogewogICAgICAgICAgInJlbGF0aW9uIjogInVuc3BlY2lmaWVkIiwKICAgICAgICAgICJxdWFudGl0eSI6IG51bGwsCiAgICAgICAgICAidW5pdCI6IG51bGwsCiAgICAgICAgICAicmVmZXJlbmNlX2V2ZW50IjogbnVsbCwKICAgICAgICAgICJyYXdfdGV4dCI6ICIiCiAgICAgICAgfSwKICAgICAgICAibG9naWNfZ3JvdXAiOiB7CiAgICAgICAgICAiZ3JvdXBfaWQiOiAiRUcwMDEiLAogICAgICAgICAgImNvbm5lY3RvciI6ICJzaW5nbGUiLAogICAgICAgICAgInBhcmVudF9ncm91cF9pZCI6IG51bGwKICAgICAgICB9LAogICAgICAgICJldmlkZW5jZSI6IHsKICAgICAgICAgICJzdGFydF9jaGFyIjogNjEsCiAgICAgICAgICAiZW5kX2NoYXIiOiA3MCwKICAgICAgICAgICJxdW90ZSI6ICJQcmVnbmFuY3kiCiAgICAgICAgfQogICAgICB9CiAgICBdLAogICAgImFtYmlndWl0aWVzIjogW10KICB9LAogICJwcm92ZW5hbmNlIjogewogICAgImtpbmQiOiAic3ludGhldGljIiwKICAgICJhbm5vdGF0aW9uX21ldGhvZCI6ICJtYW51YWxseSBzcGVjaWZpZWQgZGV0ZXJtaW5pc3RpYyBmaXh0dXJlIgogIH0KfQoK"  # noqa: E501
_RESULT_PREFIX = "CRITERIABENCH_JOB_RESULT="


def _safe_result(artifact: dict[str, Any]) -> dict[str, Any]:
    result = artifact["results"][0]
    summary: dict[str, Any] = {
        "status": artifact["status"],
        "provider": artifact["provider"],
        "model": artifact["model"],
        "paid": artifact["paid"],
        "evaluated_cases": artifact["evaluated_cases"],
        "authorization_guard_usd": artifact["authorization_guard_usd"],
        "projected_authorization_usd": artifact["projected_authorization_usd"],
        "authorization_consumed_usd": artifact["authorization_consumed_usd"],
        "usage_priced_cost_usd": artifact["total_usage_priced_cost_usd"],
        "max_attempts_per_case": artifact["max_attempts_per_case"],
        "extraction_contract_sha256": artifact["extraction_contract_sha256"],
        "evaluation_contract_sha256": artifact["evaluation_contract_sha256"],
        "fixture_sha256": result["fixture_sha256"],
        "image_digest": os.environ.get("CRITERIABENCH_IMAGE_DIGEST", "unknown"),
        "job_runner_sha256": os.environ.get("CRITERIABENCH_JOB_RUNNER_SHA256", "unknown"),
    }
    if result["status"] == "completed":
        evaluation = result["evaluation"]
        extraction = result["extraction"]
        summary.update(
            {
                "input_tokens": result["input_tokens"],
                "output_tokens": result["output_tokens"],
                "latency_ms": result["latency_ms"],
                "inclusion_count": len(extraction["inclusion_criteria"]),
                "exclusion_count": len(extraction["exclusion_criteria"]),
                "schema_valid": evaluation["schema_valid"],
                "exact_match_f1": evaluation["exact_match_f1"],
                "token_f1": evaluation["token_f1"],
                "macro_field_accuracy": evaluation["macro_field_accuracy"],
                "predicted_count": evaluation["predicted_count"],
                "reference_count": evaluation["reference_count"],
            }
        )
    else:
        summary["error_type"] = result.get("error_type", "unknown")
        if "error_code" in result:
            summary["error_code"] = result["error_code"]
            summary["error_details"] = result["error_details"]
    return summary


async def _run() -> int:
    settings = Settings(_env_file=None)
    args = argparse.Namespace(
        live=True,
        acknowledge_paid_api=True,
        budget_usd=_BUDGET_USD,
    )
    settings, budget = benchmark_cli.validate_mode(args)
    if settings.openai_max_retries != 0:
        raise ValueError("production job requires zero provider retries")

    fixture_bytes = base64.b64decode(_FIXTURE_B64, validate=True)
    if hashlib.sha256(fixture_bytes).hexdigest() != _FIXTURE_SHA256:
        raise ValueError("embedded fixture hash mismatch")

    with tempfile.TemporaryDirectory(prefix="criteriabench-job-") as temporary:
        live_root = Path(temporary) / "data" / "synthetic"
        live_root.mkdir(parents=True)
        fixture_path = live_root / _FIXTURE_NAME
        fixture_path.write_bytes(fixture_bytes)
        manifest = {
            "dataset_version": "synthetic.2026-08-31.v1-fixed-job",
            "records": [
                {
                    "path": _FIXTURE_NAME,
                    "sha256": _FIXTURE_SHA256,
                    "has_reference": True,
                }
            ],
        }
        (live_root / "manifest.json").write_text(
            json.dumps(manifest, separators=(",", ":")) + "\n",
            encoding="utf-8",
        )

        previous_roots = benchmark_cli.LIVE_DATA_ROOTS
        benchmark_cli.LIVE_DATA_ROOTS = (live_root,)
        try:
            artifact = await benchmark_cli.run(
                [fixture_path],
                settings=settings,
                budget_usd=budget,
            )
        finally:
            benchmark_cli.LIVE_DATA_ROOTS = previous_roots

    summary = _safe_result(artifact)
    print(_RESULT_PREFIX + json.dumps(summary, sort_keys=True, separators=(",", ":")))
    return 0 if summary["status"] == "completed" else 1


def main() -> None:
    try:
        exit_code = asyncio.run(_run())
    except Exception as exc:
        safe = {"status": "failed", "error_type": type(exc).__name__}
        print(_RESULT_PREFIX + json.dumps(safe, sort_keys=True, separators=(",", ":")))
        raise SystemExit(1) from None
    raise SystemExit(exit_code)


if __name__ == "__main__":
    main()
