"""Console entrypoints without implicit dotenv loading."""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

import uvicorn

from criteriabench.clinicaltrials import ClinicalTrialsClient
from criteriabench.worker.runtime import run_worker


def api() -> None:
    parser = argparse.ArgumentParser(description="Run the local CriteriaBench API")
    parser.add_argument(
        "--host",
        default="127.0.0.1",
        help="bind address (default: loopback; opt in explicitly to wider exposure)",
    )
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()
    uvicorn.run(
        "criteriabench.api.app:create_app",
        factory=True,
        host=args.host,
        port=args.port,
    )


def worker() -> None:
    asyncio.run(run_worker())


def download() -> None:
    parser = argparse.ArgumentParser(description="Download public ClinicalTrials.gov records")
    parser.add_argument("nct_ids", nargs="+", help="one or more NCT identifiers")
    parser.add_argument("--output", type=Path, default=Path("data/public"))
    args = parser.parse_args()
    asyncio.run(_download(args.nct_ids, args.output))


async def _download(nct_ids: list[str], output: Path) -> None:
    await asyncio.to_thread(output.mkdir, parents=True, exist_ok=True)
    async with ClinicalTrialsClient() as client:
        for nct_id in nct_ids:
            trial = await client.fetch_trial(nct_id)
            destination = output / f"{trial.trial_id}.json"
            serialized = (
                json.dumps(trial.model_dump(mode="json"), indent=2, ensure_ascii=False) + "\n"
            )
            await asyncio.to_thread(
                destination.write_text,
                serialized,
                encoding="utf-8",
            )
