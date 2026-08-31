"""Run with ``python -m criteriabench.worker``."""

import asyncio

from criteriabench.worker.runtime import run_worker

if __name__ == "__main__":
    asyncio.run(run_worker())
