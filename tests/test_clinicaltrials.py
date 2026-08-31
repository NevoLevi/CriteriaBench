from __future__ import annotations

import httpx
import pytest

from criteriabench.clinicaltrials import ClinicalTrialsClient, ClinicalTrialsError, map_study


def test_maps_api_v2_modules() -> None:
    result = map_study(
        {
            "protocolSection": {
                "identificationModule": {
                    "nctId": "NCT00000001",
                    "briefTitle": "Public test record",
                },
                "eligibilityModule": {"eligibilityCriteria": "Inclusion Criteria:\n- Adult"},
            }
        }
    )
    assert result.trial_id == "NCT00000001"
    assert result.source_url == "https://clinicaltrials.gov/study/NCT00000001"


async def test_client_uses_fixed_host_and_rejects_invalid_ids_without_network() -> None:
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: httpx.Response(500)),
        base_url="https://clinicaltrials.gov/api/v2",
    ) as client:
        api = ClinicalTrialsClient(client=client)
        with pytest.raises(ValueError, match="NCT"):
            await api.fetch_trial("https://example.com/private")


def test_missing_eligibility_is_a_safe_mapping_error() -> None:
    with pytest.raises(ClinicalTrialsError):
        map_study({"protocolSection": {}})
