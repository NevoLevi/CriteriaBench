"""Client for the public ClinicalTrials.gov API v2."""

from __future__ import annotations

import json
import re
from typing import Any

import httpx
from pydantic import ValidationError

from criteriabench.domain.schemas import TrialDocument

_NCT_ID = re.compile(r"^NCT[0-9]{8}$")


class ClinicalTrialsError(RuntimeError):
    """A safe error while retrieving or mapping a public study record."""


class ClinicalTrialsClient:
    base_url = "https://clinicaltrials.gov/api/v2/"

    def __init__(
        self,
        *,
        timeout_seconds: float = 20.0,
        max_response_bytes: int = 2_000_000,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        if max_response_bytes <= 0:
            raise ValueError("max_response_bytes must be positive")
        self._owns_client = client is None
        self._max_response_bytes = max_response_bytes
        self._client = client or httpx.AsyncClient(
            base_url=self.base_url,
            timeout=timeout_seconds,
            headers={"User-Agent": "CriteriaBench/0.1 (public research benchmark)"},
            follow_redirects=False,
        )

    async def fetch_trial(self, nct_id: str) -> TrialDocument:
        normalized_id = nct_id.upper()
        if not _NCT_ID.fullmatch(normalized_id):
            raise ValueError("nct_id must match NCT followed by eight digits")
        try:
            async with self._client.stream("GET", f"studies/{normalized_id}") as response:
                response.raise_for_status()
                advertised_size = response.headers.get("content-length")
                if advertised_size is not None and int(advertised_size) > self._max_response_bytes:
                    raise ClinicalTrialsError("study response exceeds the configured size limit")
                body = bytearray()
                async for chunk in response.aiter_bytes():
                    body.extend(chunk)
                    if len(body) > self._max_response_bytes:
                        raise ClinicalTrialsError(
                            "study response exceeds the configured size limit"
                        )
                payload = json.loads(body)
        except ClinicalTrialsError:
            raise
        except (httpx.HTTPError, ValueError, TypeError) as exc:
            raise ClinicalTrialsError("could not retrieve the public study record") from exc

        trial = map_study(payload)
        if trial.trial_id != normalized_id:
            raise ClinicalTrialsError("returned study identifier does not match request")
        return trial

    async def close(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def __aenter__(self) -> ClinicalTrialsClient:
        return self

    async def __aexit__(self, *args: object) -> None:
        await self.close()


def map_study(payload: dict[str, Any]) -> TrialDocument:
    """Map the required API v2 fields into the strict benchmark boundary."""

    try:
        protocol = payload["protocolSection"]
        identification = protocol["identificationModule"]
        eligibility = protocol["eligibilityModule"]
        nct_id = identification["nctId"]
        title = identification["briefTitle"]
        eligibility_text = eligibility["eligibilityCriteria"]
        return TrialDocument(
            trial_id=nct_id,
            title=title,
            eligibility_text=eligibility_text,
            source_url=f"https://clinicaltrials.gov/study/{nct_id}",
        )
    except (KeyError, TypeError, ValidationError) as exc:
        raise ClinicalTrialsError(
            "study record does not contain valid required eligibility fields"
        ) from exc
