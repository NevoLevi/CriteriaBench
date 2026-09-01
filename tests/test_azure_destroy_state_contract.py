import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _powershell() -> str | None:
    return shutil.which("pwsh") or shutil.which("powershell")


def _managed_addresses(tmp_path: Path, addresses: list[str]) -> list[str]:
    shell = _powershell()
    if shell is None:
        pytest.skip("PowerShell is required for Terraform state classifier tests")

    fixture_path = tmp_path / "state-addresses.json"
    fixture_path.write_text(json.dumps(addresses), encoding="utf-8")
    runner = tmp_path / "state-runner.ps1"
    runner.write_text(
        """param(
    [Parameter(Mandatory)][string]$SafetyPath,
    [Parameter(Mandatory)][string]$FixturePath
)
$ErrorActionPreference = "Stop"
. $SafetyPath
$parsedAddresses = Get-Content -Raw -LiteralPath $FixturePath | ConvertFrom-Json
$addresses = [string[]]$parsedAddresses
$managed = @(Get-CriteriaBenchTerraformManagedStateEntry -Address $addresses)
[ordered]@{ managed = $managed } | ConvertTo-Json -Compress -Depth 5
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
    return list(json.loads(completed.stdout.strip())["managed"])


def test_destroy_blocks_only_remaining_managed_state() -> None:
    script = (ROOT / "scripts" / "azure-destroy.ps1").read_text(encoding="utf-8")

    assert "Get-CriteriaBenchTerraformManagedStateEntry" in script
    assert "if ($remainingManagedState.Count -gt 0)" in script
    assert "if (@($remainingState).Count -gt 0)" not in script
    assert script.index("if ($LASTEXITCODE -ne 0)") < script.index(
        "Get-CriteriaBenchTerraformManagedStateEntry"
    )


def test_data_source_only_state_is_accepted(tmp_path: Path) -> None:
    addresses = [
        "data.azurerm_client_config.current",
        "data.azurerm_subscription.current",
        "module.platform.data.azurerm_client_config.current",
        'module.platform["west.eu"].module.network["apps]blue"].data.azurerm_subnet.selected["api.v1"]',
        "   ",
    ]

    assert _managed_addresses(tmp_path, addresses) == []


def test_managed_malformed_and_unknown_state_still_block_teardown(tmp_path: Path) -> None:
    addresses = [
        "data.azurerm_client_config.current",
        "azurerm_resource_group.parent",
        "module.platform.azurerm_kubernetes_cluster.main",
        "module.data.azurerm_resource_group.named_data",
        "terraform_data.guard",
        "DATA.azurerm_client_config.current",
        "data.azurerm_client_config",
    ]

    assert _managed_addresses(tmp_path, addresses) == addresses[1:]
