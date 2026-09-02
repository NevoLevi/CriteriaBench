[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)] [string]$ArtifactRoot,
    [Parameter(Mandatory = $true)] [string]$Image,
    [Parameter(Mandatory = $true)] [string]$PreregistrationPath,
    [Parameter(Mandatory = $true)] [string]$ExecutionBindingPath,
    [Parameter(Mandatory = $true)] [string]$PlanPath,
    [Parameter(Mandatory = $true)] [string]$AuthorizationPath,
    [Parameter(Mandatory = $true)] [string]$ScoreReportPath,
    [string]$DecisionFileName = 'llf-canary-advancement-decision.json'
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

function Resolve-ExistingFile {
    param([string]$Path, [string]$Label)
    $resolved = (Resolve-Path -LiteralPath $Path -ErrorAction Stop).Path
    if (-not [System.IO.File]::Exists($resolved)) {
        throw "$Label must be an existing regular file"
    }
    return $resolved
}

if ([System.IO.Path]::GetFileName($DecisionFileName) -ne $DecisionFileName) {
    throw 'DecisionFileName must be a direct filename'
}
$artifactRootResolved = (Resolve-Path -LiteralPath $ArtifactRoot -ErrorAction Stop).Path
$preregistrationResolved = Resolve-ExistingFile -Path $PreregistrationPath -Label 'PreregistrationPath'
$executionBindingResolved = Resolve-ExistingFile -Path $ExecutionBindingPath -Label 'ExecutionBindingPath'
$planResolved = Resolve-ExistingFile -Path $PlanPath -Label 'PlanPath'
$authorizationResolved = Resolve-ExistingFile -Path $AuthorizationPath -Label 'AuthorizationPath'
$scoreReportResolved = Resolve-ExistingFile -Path $ScoreReportPath -Label 'ScoreReportPath'

$imageId = (& docker image inspect --format '{{.Id}}' $Image).Trim()
if ($LASTEXITCODE -ne 0 -or $imageId -notmatch '^sha256:[0-9a-f]{64}$') {
    throw 'Image must resolve to one exact local sha256 image ID'
}
$plan = Get-Content -Raw -LiteralPath $planResolved | ConvertFrom-Json
if ([string]$plan.runtime_image_id -cne $imageId) {
    throw 'Resolved image ID differs from the exact image sealed in the canary plan'
}

$commonDockerOptions = @(
    '--rm', '--read-only', '--cap-drop=ALL',
    '--security-opt=no-new-privileges', '--pids-limit=128',
    '--tmpfs=/tmp:rw,noexec,nosuid,nodev,size=16m', '--network=none',
    '--mount', "type=bind,src=$artifactRootResolved,dst=/run/artifacts",
    '--mount', "type=bind,src=$preregistrationResolved,dst=/run/bind/preregistration.json,readonly",
    '--mount', "type=bind,src=$executionBindingResolved,dst=/run/bind/execution-binding.json,readonly",
    '--mount', "type=bind,src=$planResolved,dst=/run/bind/plan.json,readonly",
    '--mount', "type=bind,src=$authorizationResolved,dst=/run/bind/authorization.json,readonly",
    '--mount', "type=bind,src=$scoreReportResolved,dst=/run/bind/score-report.json,readonly",
    '--entrypoint', '/opt/venv/bin/criteriabench-llf-canary-preregister',
    $imageId
)
$chainArguments = @(
    '--preregistration', '/run/bind/preregistration.json',
    '--execution-binding', '/run/bind/execution-binding.json',
    '--plan', '/run/bind/plan.json',
    '--authorization', '/run/bind/authorization.json',
    '--score-report', '/run/bind/score-report.json'
)

& docker run @commonDockerOptions decide @chainArguments --output "/run/artifacts/$DecisionFileName"
if ($LASTEXITCODE -ne 0) {
    throw "Offline advancement-decision container exited with code $LASTEXITCODE"
}
$decisionPath = Join-Path $artifactRootResolved $DecisionFileName
& docker run @commonDockerOptions check-decision @chainArguments --artifact "/run/artifacts/$DecisionFileName"
if ($LASTEXITCODE -ne 0) {
    throw "Offline advancement-decision verification exited with code $LASTEXITCODE"
}

$decision = Get-Content -Raw -LiteralPath $decisionPath | ConvertFrom-Json
Write-Output "Advancement decision path: $decisionPath"
Write-Output "Advancement decision SHA256: $($decision.decision_sha256)"
Write-Output "Advancement status: $($decision.advancement_status)"
Write-Output "Proceed to separate locked planning: $($decision.proceed_to_separate_locked_authorization)"
if ($decision.advancement_status -cne 'pass' -or $decision.proceed_to_separate_locked_authorization -ne $true) {
    Write-Output 'The exact canary chain did not pass every preregistered gate. Locked planning remains blocked.'
}
