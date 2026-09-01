import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _powershell() -> str | None:
    return shutil.which("pwsh") or shutil.which("powershell")


def test_all_terraform_azure_entrypoints_use_scoped_cli_path() -> None:
    tooling = (ROOT / "scripts" / "tooling.ps1").read_text(encoding="utf-8")
    assert "function Invoke-CriteriaBenchWithAzureCliOnPath" in tooling
    assert "Get-Command az -CommandType Application" in tooling
    assert '[Environment]::SetEnvironmentVariable("PATH", $scopedPath, "Process")' in tooling
    assert '[Environment]::SetEnvironmentVariable("PATH", $previousPath, "Process")' in tooling

    invocation = "Invoke-CriteriaBenchWithAzureCliOnPath -AzPath $az -Action {"
    for relative_path in (
        "scripts/azure-plan.ps1",
        "scripts/azure-apply-reviewed.ps1",
        "scripts/azure-destroy.ps1",
    ):
        script = (ROOT / relative_path).read_text(encoding="utf-8")
        assert invocation in script, relative_path


def test_scoped_cli_path_selects_cmd_launcher_and_restores_path(tmp_path: Path) -> None:
    shell = _powershell()
    if shell is None:
        pytest.skip("PowerShell is required for the scoped Azure CLI behavior test")

    launcher_dir = tmp_path / "azure cli with spaces"
    launcher_dir.mkdir()
    if os.name == "nt":
        launcher = launcher_dir / "az.cmd"
        launcher.write_text(
            "@echo off\r\necho fake-azure-cli\r\nexit /b 0\r\n",
            encoding="utf-8",
        )
    else:
        launcher = launcher_dir / "az"
        launcher.write_text("#!/bin/sh\necho fake-azure-cli\nexit 0\n", encoding="utf-8")
        launcher.chmod(0o755)

    runner = tmp_path / "verify-azure-cli-path.ps1"
    runner.write_text(
        """param(
    [Parameter(Mandatory)][string]$ToolingPath,
    [Parameter(Mandatory)][string]$LauncherPath
)
$ErrorActionPreference = "Stop"
. $ToolingPath
$before = [Environment]::GetEnvironmentVariable("PATH", "Process")
$inside = Invoke-CriteriaBenchWithAzureCliOnPath -AzPath $LauncherPath -Action {
    $visible = Get-Command az -CommandType Application -ErrorAction Stop |
        Select-Object -First 1
    $childOutput = & az
    if ($LASTEXITCODE -ne 0) {
        throw "Fake Azure CLI launcher failed with exit code $LASTEXITCODE."
    }
    [ordered]@{
        path = [Environment]::GetEnvironmentVariable("PATH", "Process")
        resolved = $visible.Source
        child_output = ($childOutput | Out-String).Trim()
    }
}
$afterSuccess = [Environment]::GetEnvironmentVariable("PATH", "Process")
$caught = $false
try {
    Invoke-CriteriaBenchWithAzureCliOnPath -AzPath $LauncherPath -Action {
        throw "expected-sentinel"
    }
}
catch {
    $caught = $_.Exception.Message -eq "expected-sentinel"
}
$afterFailure = [Environment]::GetEnvironmentVariable("PATH", "Process")
[ordered]@{
    before = $before
    inside_path = $inside.path
    resolved = $inside.resolved
    child_output = $inside.child_output
    after_success = $afterSuccess
    failure_observed = $caught
    after_failure = $afterFailure
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
            str(ROOT / "scripts" / "tooling.ps1"),
            str(launcher),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    result = json.loads(completed.stdout.strip())

    assert result["before"] == result["after_success"] == result["after_failure"]
    assert result["failure_observed"] is True
    assert result["child_output"] == "fake-azure-cli"
    assert os.path.normcase(os.path.abspath(result["resolved"])) == os.path.normcase(
        os.path.abspath(launcher)
    )
    assert os.path.normcase(
        result["inside_path"].split(os.pathsep, maxsplit=1)[0]
    ) == os.path.normcase(str(launcher_dir))
