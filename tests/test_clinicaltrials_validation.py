from __future__ import annotations

import pytest

from criteriabench.clinicaltrials import ClinicalTrialsError, map_study


def test_mapping_translates_upstream_type_drift_to_safe_error() -> None:
    payload = {
        "protocolSection": {
            "identificationModule": {
                "nctId": "NCT00000001",
                "briefTitle": ["not", "a", "string"],
            },
            "eligibilityModule": {"eligibilityCriteria": "Adult"},
        }
    }
    with pytest.raises(ClinicalTrialsError, match="valid required"):
        map_study(payload)
