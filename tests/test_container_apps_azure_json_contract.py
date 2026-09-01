from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.skipif(os.name != "nt", reason="The regression targets Windows az.cmd")
def test_fake_az_cmd_full_json_contract_and_ps5_array_enumeration(
    tmp_path: Path,
) -> None:
    shell = shutil.which("powershell")
    if shell is None:
        pytest.skip("Windows PowerShell 5 is required for the az.cmd regression")

    launcher_dir = tmp_path / "fake azure cli with spaces"
    launcher_dir.mkdir()
    launcher = launcher_dir / "az.cmd"
    fake_script = launcher_dir / "fake-az.ps1"
    invocation_log = tmp_path / "fake az invocations.jsonl"

    launcher.write_text(
        """@echo off\r
setlocal\r
set "CRITERIABENCH_FAKE_AZ_CMD_PATH=%~f0"\r
"%SystemRoot%\\System32\\WindowsPowerShell\\v1.0\\powershell.exe" -NoLogo ^\r
-NoProfile -NonInteractive -File "%~dp0fake-az.ps1" %*\r
exit /b %errorlevel%\r
""",
        encoding="utf-8",
    )
    fake_script.write_text(
        r"""param(
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$RemainingArguments
)
$ErrorActionPreference = "Stop"
if ($RemainingArguments -contains "--query") {
    exit 91
}
foreach ($argument in $RemainingArguments) {
    if ($argument -match '[{}\[\]|@()]') {
        exit 92
    }
}

$record = [ordered]@{
    launcher = $env:CRITERIABENCH_FAKE_AZ_CMD_PATH
    arguments = @($RemainingArguments)
}
$encoding = New-Object System.Text.UTF8Encoding($false)
[IO.File]::AppendAllText(
    $env:CRITERIABENCH_FAKE_AZ_LOG,
    (($record | ConvertTo-Json -Compress) + [Environment]::NewLine),
    $encoding
)

function Get-ArgumentValue([string]$Name) {
    $index = [Array]::IndexOf($RemainingArguments, $Name)
    if ($index -lt 0 -or $index + 1 -ge $RemainingArguments.Count) {
        return ""
    }
    return $RemainingArguments[$index + 1]
}

if ($RemainingArguments.Count -ge 2 -and
    $RemainingArguments[0] -eq "account" -and
    $RemainingArguments[1] -eq "show") {
    [Console]::Out.WriteLine('{"state":"Enabled","user":{"name":"tester@example.com"},"internal":"DO-NOT-PRINT-SENTINEL"}')
    exit 0
}
if ($RemainingArguments.Count -ge 3 -and
    $RemainingArguments[0] -eq "containerapp" -and
    $RemainingArguments[1] -eq "job" -and
    $RemainingArguments[2] -eq "show") {
    [Console]::Out.WriteLine('{"properties":{"configuration":{"triggerType":"Manual","replicaRetryLimit":0,"replicaTimeout":300,"manualTriggerConfig":{"parallelism":1,"replicaCompletionCount":1}},"template":{"containers":[{"name":"criteriabench-live","image":"ghcr.io/nevolevi/criteriabench@sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"}]}},"internal":"DO-NOT-PRINT-SENTINEL"}')
    exit 0
}
if ($RemainingArguments.Count -ge 5 -and
    $RemainingArguments[0] -eq "containerapp" -and
    $RemainingArguments[1] -eq "job" -and
    $RemainingArguments[2] -eq "execution" -and
    $RemainingArguments[3] -eq "list") {
    $jobName = Get-ArgumentValue "--name"
    if ($jobName -eq "one-job") {
        [Console]::Out.WriteLine('[{"name":"one-execution","internal":"DO-NOT-PRINT-SENTINEL"}]')
    }
    elseif ($jobName -eq "many-job") {
        [Console]::Out.WriteLine('[{"name":"first-execution"},{"name":"second-execution"}]')
    }
    else {
        [Console]::Out.WriteLine('[]')
    }
    exit 0
}
if ($RemainingArguments.Count -ge 3 -and
    $RemainingArguments[0] -eq "containerapp" -and
    $RemainingArguments[1] -eq "job" -and
    $RemainingArguments[2] -eq "start") {
    [Console]::Out.WriteLine('{"name":"run-123","internal":"DO-NOT-PRINT-SENTINEL"}')
    exit 0
}
if ($RemainingArguments.Count -ge 5 -and
    $RemainingArguments[0] -eq "containerapp" -and
    $RemainingArguments[1] -eq "job" -and
    $RemainingArguments[2] -eq "execution" -and
    $RemainingArguments[3] -eq "show") {
    [Console]::Out.WriteLine('{"properties":{"status":"Succeeded"},"internal":"DO-NOT-PRINT-SENTINEL"}')
    exit 0
}
if ($RemainingArguments.Count -ge 2 -and
    $RemainingArguments[0] -eq "keyvault" -and
    $RemainingArguments[1] -eq "list-deleted") {
    [Console]::Out.WriteLine('[{"name":"target-vault"},{"name":"target-vault-extra"}]')
    exit 0
}
exit 93
""",
        encoding="utf-8",
    )

    runner = tmp_path / "verify full json parsing.ps1"
    runner.write_text(
        r"""param(
    [Parameter(Mandatory)][string]$AzureSafetyPath,
    [Parameter(Mandatory)][string]$ContainerSafetyPath,
    [Parameter(Mandatory)][string]$LauncherPath,
    [Parameter(Mandatory)][string]$LogPath
)
$ErrorActionPreference = "Stop"
. $AzureSafetyPath
. $ContainerSafetyPath
$env:CRITERIABENCH_FAKE_AZ_LOG = $LogPath

$account = Get-CriteriaBenchAzureAccountSnapshot -AzPath $LauncherPath
$job = Get-CriteriaBenchContainerAppJobContract `
    -AzPath $LauncherPath -JobName "contract-job" -ResourceGroup "group-with-spaces"
$one = @(Get-CriteriaBenchContainerAppJobExecutions `
        -AzPath $LauncherPath -JobName "one-job" -ResourceGroup "group-with-spaces")
$many = @(Get-CriteriaBenchContainerAppJobExecutions `
        -AzPath $LauncherPath -JobName "many-job" -ResourceGroup "group-with-spaces")
$empty = @(Get-CriteriaBenchContainerAppJobExecutions `
        -AzPath $LauncherPath -JobName "empty-job" -ResourceGroup "group-with-spaces")
$started = Start-CriteriaBenchContainerAppJobExecution `
    -AzPath $LauncherPath -JobName "contract-job" -ResourceGroup "group-with-spaces"
$status = Get-CriteriaBenchContainerAppJobExecutionStatus `
    -AzPath $LauncherPath -JobName "contract-job" -ResourceGroup "group-with-spaces" `
    -ExecutionName $started
$deletedCount = Get-CriteriaBenchDeletedKeyVaultExactMatchCount `
    -AzPath $LauncherPath -VaultName "target-vault"

& $LauncherPath account show --query state --output json --only-show-errors
$queryRejected = $LASTEXITCODE -eq 91
& $LauncherPath account show 'hostile{projection}' --output json --only-show-errors
$hostileRejected = $LASTEXITCODE -eq 92

[ordered]@{
    ps_major = $PSVersionTable.PSVersion.Major
    account_state = $account.State
    account_user = $account.UserName
    trigger = $job.TriggerType
    retry = $job.ReplicaRetryLimit
    timeout = $job.ReplicaTimeout
    parallelism = $job.Parallelism
    completions = $job.ReplicaCompletionCount
    container_name = $job.Name
    image = $job.Image
    one_count = $one.Count
    one_name = $one[0].name
    many_count = $many.Count
    many_names = @($many | ForEach-Object { $_.name })
    empty_count = $empty.Count
    started = $started
    status = $status
    deleted_count = $deletedCount
    query_rejected = $queryRejected
    hostile_rejected = $hostileRejected
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
            "-AzureSafetyPath",
            str(ROOT / "scripts" / "azure-safety.ps1"),
            "-ContainerSafetyPath",
            str(ROOT / "scripts" / "container-apps-safety.ps1"),
            "-LauncherPath",
            str(launcher),
            "-LogPath",
            str(invocation_log),
        ],
        check=True,
        capture_output=True,
        text=True,
    )

    assert "DO-NOT-PRINT-SENTINEL" not in completed.stdout
    assert "DO-NOT-PRINT-SENTINEL" not in completed.stderr
    result = json.loads(completed.stdout.strip())
    assert result == {
        "ps_major": 5,
        "account_state": "Enabled",
        "account_user": "tester@example.com",
        "trigger": "Manual",
        "retry": 0,
        "timeout": 300,
        "parallelism": 1,
        "completions": 1,
        "container_name": "criteriabench-live",
        "image": "ghcr.io/nevolevi/criteriabench@sha256:" + "a" * 64,
        "one_count": 1,
        "one_name": "one-execution",
        "many_count": 2,
        "many_names": ["first-execution", "second-execution"],
        "empty_count": 0,
        "started": "run-123",
        "status": "Succeeded",
        "deleted_count": 1,
        "query_rejected": True,
        "hostile_rejected": True,
    }

    records = [
        json.loads(line)
        for line in invocation_log.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert len(records) == 8
    assert all("--query" not in record["arguments"] for record in records)
    assert all(
        os.path.normcase(os.path.abspath(record["launcher"]))
        == os.path.normcase(os.path.abspath(launcher))
        for record in records
    )


def test_production_scripts_have_no_complex_azure_cli_queries() -> None:
    helper = (ROOT / "scripts" / "container-apps-safety.ps1").read_text(encoding="utf-8")
    deploy = (ROOT / "scripts" / "container-apps-deploy.ps1").read_text(encoding="utf-8")
    destroy = (ROOT / "scripts" / "container-apps-destroy.ps1").read_text(encoding="utf-8")

    forbidden = ("{trigger:", "length(@)", "[?name==", "properties.status")
    literal_query = re.compile(r'"--query"\s*,\s*"([^"]*)"')
    hostile_query_character = re.compile(r"[{}\[\]|@()]")
    for source in (helper, deploy, destroy):
        assert all(fragment not in source for fragment in forbidden)
        for query in literal_query.findall(source):
            assert hostile_query_character.search(query) is None
    assert helper.count('"--query"') == 1
    assert deploy.count('"--query"') == 1
    assert '"--query"' not in destroy
