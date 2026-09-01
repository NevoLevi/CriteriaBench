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


def test_apply_scopes_kubelogin_around_all_credential_consumers() -> None:
    script = (ROOT / "scripts" / "azure-apply-reviewed.ps1").read_text(encoding="utf-8")
    invocation = "Invoke-CriteriaBenchWithKubeloginOnPath -KubeloginPath $kubelogin -Action {"
    scope_start = script.index(invocation)
    catch_start = script.index("\n    catch {", scope_start)
    consumer_scope = script[scope_start:catch_start]

    assert script.index('"convert-kubeconfig"') < scope_start
    for expected in (
        "$kubectl auth can-i get pods",
        "$kubectl create namespace criteriabench",
        "Invoke-CriteriaBenchNative -FilePath $kubectl",
        "Invoke-CriteriaBenchNative -FilePath $helm",
        '"deployment/criteriabench-api"',
        '"deployment/criteriabench-worker"',
    ):
        assert expected in consumer_scope

    cleanup_scope = script[catch_start:]
    assert "confirm_billable_deployment=false" in cleanup_scope
    assert '[Environment]::SetEnvironmentVariable("KUBECONFIG"' in cleanup_scope


def test_kubelogin_is_exactly_resolved_in_child_and_path_is_restored(
    tmp_path: Path,
) -> None:
    shell = _powershell()
    if shell is None:
        pytest.skip("PowerShell is required for kubelogin PATH behavior tests")

    launcher_dir = tmp_path / "kubelogin launcher with spaces"
    launcher_dir.mkdir()
    sentinel_dir = tmp_path / "existing azure path with spaces"
    sentinel_dir.mkdir()
    if os.name == "nt":
        launcher = launcher_dir / "kubelogin.cmd"
        launcher.write_text(
            "@echo off\r\necho child-kubelogin-ok\r\nexit /b 0\r\n",
            encoding="utf-8",
        )
    else:
        launcher = launcher_dir / "kubelogin"
        launcher.write_text("#!/bin/sh\necho child-kubelogin-ok\n", encoding="utf-8")
        launcher.chmod(0o755)

    child_probe = tmp_path / "child-probe.ps1"
    child_probe.write_text(
        """$ErrorActionPreference = "Stop"
$visible = Get-Command kubelogin -CommandType Application -ErrorAction Stop |
    Select-Object -First 1
$childOutput = & kubelogin
if ($LASTEXITCODE -ne 0) {
    throw "Fake kubelogin launcher failed with exit code $LASTEXITCODE."
}
[ordered]@{
    resolved = $visible.Source
    output = ($childOutput | Out-String).Trim()
} | ConvertTo-Json -Compress
""",
        encoding="utf-8",
    )

    runner = tmp_path / "verify-kubelogin-path.ps1"
    runner.write_text(
        """param(
    [Parameter(Mandatory)][string]$ToolingPath,
    [Parameter(Mandatory)][string]$LauncherPath,
    [Parameter(Mandatory)][string]$SentinelDirectory,
    [Parameter(Mandatory)][string]$ChildPowerShell,
    [Parameter(Mandatory)][string]$ChildProbe
)
$ErrorActionPreference = "Stop"
. $ToolingPath
$processStartPath = [Environment]::GetEnvironmentVariable("PATH", "Process")
$separator = [string][IO.Path]::PathSeparator
$outerPath = $SentinelDirectory + $separator + $processStartPath
$result = $null
try {
    [Environment]::SetEnvironmentVariable("PATH", $outerPath, "Process")
    $inside = Invoke-CriteriaBenchWithKubeloginOnPath -KubeloginPath $LauncherPath -Action {
        $visible = Get-Command kubelogin -CommandType Application -ErrorAction Stop |
            Select-Object -First 1
        $childJson = & $ChildPowerShell -NoLogo -NoProfile -NonInteractive -File $ChildProbe
        if ($LASTEXITCODE -ne 0) {
            throw "Child kubelogin lookup failed with exit code $LASTEXITCODE."
        }
        $child = $childJson | ConvertFrom-Json
        $pathEntries = [Environment]::GetEnvironmentVariable("PATH", "Process").Split($separator)
        [ordered]@{
            first_path = $pathEntries[0]
            second_path = $pathEntries[1]
            resolved = $visible.Source
            child_resolved = $child.resolved
            child_output = $child.output
        }
    }
    $successRestored = [Environment]::GetEnvironmentVariable("PATH", "Process") -ceq $outerPath
    $failureObserved = $false
    try {
        Invoke-CriteriaBenchWithKubeloginOnPath -KubeloginPath $LauncherPath -Action {
            throw "expected-sentinel"
        }
    }
    catch {
        $failureObserved = $_.Exception.Message -eq "expected-sentinel"
    }
    $failureRestored = [Environment]::GetEnvironmentVariable("PATH", "Process") -ceq $outerPath
    $result = [ordered]@{
        first_path = $inside.first_path
        second_path = $inside.second_path
        resolved = $inside.resolved
        child_resolved = $inside.child_resolved
        child_output = $inside.child_output
        success_restored = $successRestored
        failure_observed = $failureObserved
        failure_restored = $failureRestored
        process_restored = $false
    }
}
finally {
    [Environment]::SetEnvironmentVariable("PATH", $processStartPath, "Process")
}
$result.process_restored = (
    [Environment]::GetEnvironmentVariable("PATH", "Process") -ceq $processStartPath
)
$result | ConvertTo-Json -Compress
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
            "-ToolingPath",
            str(ROOT / "scripts" / "tooling.ps1"),
            "-LauncherPath",
            str(launcher),
            "-SentinelDirectory",
            str(sentinel_dir),
            "-ChildPowerShell",
            shell,
            "-ChildProbe",
            str(child_probe),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    result = json.loads(completed.stdout.strip())

    assert os.path.normcase(result["first_path"]) == os.path.normcase(str(launcher_dir))
    assert os.path.normcase(result["second_path"]) == os.path.normcase(str(sentinel_dir))
    expected_launcher = os.path.normcase(os.path.abspath(launcher))
    assert os.path.normcase(os.path.abspath(result["resolved"])) == expected_launcher
    assert os.path.normcase(os.path.abspath(result["child_resolved"])) == expected_launcher
    assert result["child_output"] == "child-kubelogin-ok"
    assert result["success_restored"] is True
    assert result["failure_observed"] is True
    assert result["failure_restored"] is True
    assert result["process_restored"] is True
