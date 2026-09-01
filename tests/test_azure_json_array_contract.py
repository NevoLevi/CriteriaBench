import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _powershell() -> str | None:
    if os.name == "nt":
        return shutil.which("powershell") or shutil.which("pwsh")
    return shutil.which("pwsh") or shutil.which("powershell")


def test_preflight_normalizes_both_azure_json_arrays() -> None:
    preflight = (ROOT / "scripts" / "azure-preflight.ps1").read_text(encoding="utf-8")

    assert 'Join-Path $PSScriptRoot "azure-json.ps1"' in preflight
    assert preflight.count("ConvertFrom-CriteriaBenchJsonObjectArray") == 2
    assert "@($skuText | ConvertFrom-Json)" not in preflight
    assert "@($usageText | ConvertFrom-Json)" not in preflight


def test_literal_one_and_multi_element_arrays_are_enumerated_in_powershell(
    tmp_path: Path,
) -> None:
    shell = _powershell()
    if shell is None:
        pytest.skip("PowerShell is required for Azure JSON normalization tests")

    runner = tmp_path / "json-array-runner.ps1"
    runner.write_text(
        """param([Parameter(Mandatory)][string]$JsonHelperPath)
$ErrorActionPreference = "Stop"
. $JsonHelperPath
$one = @(ConvertFrom-CriteriaBenchJsonObjectArray `
    -Json '[{"name":"one"}]' `
    -Description "one-item fixture")
$many = @(ConvertFrom-CriteriaBenchJsonObjectArray `
    -Json '[{"name":"one"},{"name":"two"}]' `
    -Description "two-item fixture")
$empty = @(ConvertFrom-CriteriaBenchJsonObjectArray `
    -Json '[]' `
    -Description "empty fixture")
$objectRejected = $false
try {
    $null = ConvertFrom-CriteriaBenchJsonObjectArray `
        -Json '{"name":"not-an-array"}' `
        -Description "object fixture"
}
catch {
    $objectRejected = $_.Exception.Message -like "*was not a JSON array*"
}
[ordered]@{
    ps_major = $PSVersionTable.PSVersion.Major
    one_count = $one.Count
    one_name = $one[0].name
    many_count = $many.Count
    names = @($many | ForEach-Object { $_.name })
    empty_count = $empty.Count
    object_rejected = $objectRejected
} | ConvertTo-Json -Compress
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
            "-JsonHelperPath",
            str(ROOT / "scripts" / "azure-json.ps1"),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    result = json.loads(completed.stdout.strip())

    if os.name == "nt":
        assert result["ps_major"] == 5
    assert result["one_count"] == 1
    assert result["one_name"] == "one"
    assert result["many_count"] == 2
    assert result["names"] == ["one", "two"]
    assert result["empty_count"] == 0
    assert result["object_rejected"] is True
