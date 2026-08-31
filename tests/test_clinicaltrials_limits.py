from __future__ import annotations

import json

import httpx
import pytest

from criteriabench.clinicaltrials import ClinicalTrialsClient, ClinicalTrialsError


def _study(nct_id: str, criteria: str = "Inclusion Criteria:\n- Adult") -> bytes:
    return json.dumps(
        {
            "protocolSection": {
                "identificationModule": {
                    "nctId": nct_id,
                    "briefTitle": "Public test record",
                },
                "eligibilityModule": {"eligibilityCriteria": criteria},
            }
        }
    ).encode()


async def test_rejects_response_advertised_above_size_limit() -> None:
    transport = httpx.MockTransport(
        lambda _: httpx.Response(
            200,
            headers={"content-length": "3000000"},
            content=_study("NCT00000001"),
        )
    )
    async with httpx.AsyncClient(transport=transport, base_url="https://clinicaltrials.gov") as raw:
        client = ClinicalTrialsClient(client=raw, max_response_bytes=2_000_000)
        with pytest.raises(ClinicalTrialsError, match="size"):
            await client.fetch_trial("NCT00000001")


async def test_rejects_response_body_above_size_limit_without_header() -> None:
    content = _study("NCT00000001", criteria="x" * 2_000_001)
    transport = httpx.MockTransport(lambda _: httpx.Response(200, content=content))
    async with httpx.AsyncClient(transport=transport, base_url="https://clinicaltrials.gov") as raw:
        client = ClinicalTrialsClient(client=raw, max_response_bytes=2_000_000)
        with pytest.raises(ClinicalTrialsError, match="size"):
            await client.fetch_trial("NCT00000001")


async def test_rejects_mismatched_returned_nct_id() -> None:
    transport = httpx.MockTransport(lambda _: httpx.Response(200, content=_study("NCT00000002")))
    async with httpx.AsyncClient(transport=transport, base_url="https://clinicaltrials.gov") as raw:
        client = ClinicalTrialsClient(client=raw)
        with pytest.raises(ClinicalTrialsError, match="identifier"):
            await client.fetch_trial("NCT00000001")
