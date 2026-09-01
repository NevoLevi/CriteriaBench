import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _powershell() -> str | None:
    return shutil.which("pwsh") or shutil.which("powershell")


def _fixture(
    *,
    family_current: int = 0,
    family_limit: int = 10,
    regional_current: int = 0,
    regional_limit: int = 10,
) -> dict[str, Any]:
    return {
        "vm_size": "Standard_D2as_v4",
        "skus": [
            {
                "name": "Standard_D2as_v4",
                "family": "standardDASv4Family",
                "vcpus": ["2"],
                "restrictions": [],
            }
        ],
        "usage": [
            {
                "name": "standardDASv4Family",
                "currentValue": family_current,
                "limit": family_limit,
            },
            {
                "name": {"value": "cores"},
                "currentValue": regional_current,
                "limit": regional_limit,
            },
        ],
    }


def _run_quota(tmp_path: Path, fixture: dict[str, Any]) -> dict[str, Any]:
    shell = _powershell()
    if shell is None:
        pytest.skip("PowerShell is required for Azure quota helper tests")

    fixture_path = tmp_path / "quota-fixture.json"
    fixture_path.write_text(json.dumps(fixture), encoding="utf-8")
    runner = tmp_path / "quota-runner.ps1"
    runner.write_text(
        """param(
    [Parameter(Mandatory)][string]$SafetyPath,
    [Parameter(Mandatory)][string]$FixturePath
)
$ErrorActionPreference = "Stop"
. $SafetyPath
$fixture = Get-Content -Raw -LiteralPath $FixturePath | ConvertFrom-Json
try {
    $quota = Assert-CriteriaBenchAzureVmQuota `
        -VmSize $fixture.vm_size `
        -SkuCandidates @($fixture.skus) `
        -UsageRecords @($fixture.usage) `
        -NodeCount 1
    [ordered]@{
        ok = $true
        family = $quota.Family
        required = $quota.RequiredVcpus
        family_remaining = $quota.FamilyRemaining
        regional_remaining = $quota.RegionalRemaining
        error = $null
    } | ConvertTo-Json -Compress
}
catch {
    [ordered]@{
        ok = $false
        error = $_.Exception.Message
    } | ConvertTo-Json -Compress
}
""",
        encoding="utf-8",
    )
    completed = subprocess.run(
        [
            shell,
            "-NoLogo",
            "-NoProfile",
            "-NonInteractive",
            "-File",
            str(runner),
            "-SafetyPath",
            str(ROOT / "scripts" / "azure-safety.ps1"),
            "-FixturePath",
            str(fixture_path),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(completed.stdout.strip())


def test_reviewed_vm_size_is_v4_everywhere() -> None:
    preflight = (ROOT / "scripts" / "azure-preflight.ps1").read_text(encoding="utf-8")
    variables = (ROOT / "infra" / "azure" / "variables.tf").read_text(encoding="utf-8")
    example = (ROOT / "infra" / "azure" / "terraform.tfvars.example").read_text(encoding="utf-8")
    readme = (ROOT / "infra" / "azure" / "README.md").read_text(encoding="utf-8")
    combined = "\n".join((preflight, variables, example, readme))

    assert "Standard_D2as_v4" in preflight
    assert 'var.node_vm_size == "Standard_D2as_v4"' in variables
    assert 'node_vm_size        = "Standard_D2as_v4"' in example
    assert "`Standard_D2as_v4`" in readme
    assert "Standard_D2as_v5" not in combined


def test_preflight_queries_sku_metadata_and_both_quota_scopes_before_plan() -> None:
    preflight = (ROOT / "scripts" / "azure-preflight.ps1").read_text(encoding="utf-8")
    plan = (ROOT / "scripts" / "azure-plan.ps1").read_text(encoding="utf-8")

    assert '"vm", "list-skus"' in preflight
    assert "family:family" in preflight
    assert "vcpus:capabilities" in preflight
    assert '"vm", "list-usage"' in preflight
    assert "Assert-CriteriaBenchAzureVmQuota" in preflight
    assert plan.index('"azure-preflight.ps1"') < plan.index('"plan", "-input=false"')


def test_quota_helper_accepts_sufficient_and_exact_boundary_capacity(tmp_path: Path) -> None:
    sufficient = _run_quota(tmp_path, _fixture())
    assert sufficient == {
        "ok": True,
        "family": "standardDASv4Family",
        "required": 2,
        "family_remaining": 10,
        "regional_remaining": 10,
        "error": None,
    }

    boundary_path = tmp_path / "boundary"
    boundary_path.mkdir()
    boundary = _run_quota(
        boundary_path,
        _fixture(family_current=8, regional_current=8),
    )
    assert boundary["ok"] is True
    assert boundary["family_remaining"] == boundary["regional_remaining"] == 2


@pytest.mark.parametrize(
    ("fixture", "expected_error"),
    [
        (_fixture(family_current=9), "Insufficient VM-family quota"),
        (_fixture(regional_current=9), "Insufficient total regional vCPU quota"),
    ],
)
def test_quota_helper_rejects_insufficient_capacity(
    tmp_path: Path,
    fixture: dict[str, Any],
    expected_error: str,
) -> None:
    result = _run_quota(tmp_path, fixture)
    assert result["ok"] is False
    assert expected_error in result["error"]
    assert "Terraform was not run" in result["error"]


@pytest.mark.parametrize(
    ("mutator", "expected_error"),
    [
        (lambda value: value["skus"].clear(), "0 exact matches"),
        (
            lambda value: value["skus"].append(dict(value["skus"][0])),
            "2 exact matches",
        ),
        (
            lambda value: value["skus"][0].update(vcpus=["2", "4"]),
            "2 vCPU capabilities",
        ),
        (
            lambda value: value["skus"][0].update(restrictions=[{"reasonCode": "QuotaId"}]),
            "is restricted",
        ),
        (
            lambda value: value["usage"].pop(0),
            "0 exact VM-family quota records",
        ),
        (
            lambda value: value["usage"].append(dict(value["usage"][1])),
            "2 exact total regional vCPU quota records",
        ),
    ],
)
def test_quota_helper_fails_closed_on_ambiguous_or_malformed_metadata(
    tmp_path: Path,
    mutator: Any,
    expected_error: str,
) -> None:
    fixture = _fixture()
    mutator(fixture)
    result = _run_quota(tmp_path, fixture)
    assert result["ok"] is False
    assert expected_error in result["error"]


def test_quota_helper_does_not_select_a_similar_sku(tmp_path: Path) -> None:
    fixture = _fixture()
    fixture["skus"][0]["name"] = "Standard_D2as_v5"

    result = _run_quota(tmp_path, fixture)

    assert result["ok"] is False
    assert "0 exact matches for SKU 'Standard_D2as_v4'" in result["error"]
