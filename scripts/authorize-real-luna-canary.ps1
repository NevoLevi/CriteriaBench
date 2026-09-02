[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)] [string]$ArtifactRoot,
    [Parameter(Mandatory = $true)] [string]$Image,
    [Parameter(Mandatory = $true)] [string]$PlanPath,
    [Parameter(Mandatory = $true)] [string]$PreregistrationPath,
    [Parameter(Mandatory = $true)] [string]$ExecutionBindingPath,
    [Parameter(Mandatory = $true)]
    [ValidatePattern('^[0-9a-f]{64}$')]
    [string]$ReviewedPlanSha256,
    [Parameter(Mandatory = $true)]
    [ValidatePattern('^[0-9a-f]{64}$')]
    [string]$ReviewedPreregistrationSha256,
    [Parameter(Mandatory = $true)]
    [ValidatePattern('^[0-9a-f]{64}$')]
    [string]$ReviewedExecutionBindingSha256,
    [Parameter(Mandatory = $true)]
    [ValidatePattern('^[0-9a-f]{64}$')]
    [string]$ReviewedCaseSetSha256,
    [Parameter(Mandatory = $true)]
    [ValidatePattern('^(?:0\.170000000|1\.250000000)$')]
    [string]$ApprovedBudgetCapUsd,
    [Parameter(Mandatory = $true)]
    [ValidateSet('I authorize this exact sealed 25-case LLF semantic paid Luna canary plan.')]
    [string]$CanaryAcknowledgement,
    [Parameter(Mandatory = $true)] [string]$AuthorizedAtUtc,
    [Parameter(Mandatory = $true)]
    [ValidatePattern('^[A-Za-z0-9]+(?:[._-][A-Za-z0-9]+)*$')]
    [string]$AuthorizationId,
    [Parameter(Mandatory = $true)]
    [ValidatePattern('^[A-Za-z0-9]+(?:[._-][A-Za-z0-9]+)*$')]
    [string]$RunId,
    [string]$AuthorizationFileName = 'llf-canary-authorization.json'
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

function Get-NormalizedHostPathSha256 {
    param([string]$Path)
    $normalized = $Path.Replace('/', '\').TrimEnd('\').ToLowerInvariant()
    $sha256 = [System.Security.Cryptography.SHA256]::Create()
    try {
        $digest = $sha256.ComputeHash([System.Text.Encoding]::UTF8.GetBytes($normalized))
        return ([System.BitConverter]::ToString($digest)).Replace('-', '').ToLowerInvariant()
    }
    finally {
        $sha256.Dispose()
    }
}

if ([System.IO.Path]::GetFileName($AuthorizationFileName) -ne $AuthorizationFileName) {
    throw 'AuthorizationFileName must be a direct filename'
}

$artifactRootResolved = (Resolve-Path -LiteralPath $ArtifactRoot -ErrorAction Stop).Path
$planResolved = (Resolve-Path -LiteralPath $PlanPath -ErrorAction Stop).Path
$preregistrationResolved = (Resolve-Path -LiteralPath $PreregistrationPath -ErrorAction Stop).Path
$executionBindingResolved = (Resolve-Path -LiteralPath $ExecutionBindingPath -ErrorAction Stop).Path
$artifactPrefix = $artifactRootResolved.TrimEnd([System.IO.Path]::DirectorySeparatorChar) + [System.IO.Path]::DirectorySeparatorChar
foreach ($reviewedPath in @($planResolved, $preregistrationResolved, $executionBindingResolved)) {
    if (-not $reviewedPath.StartsWith($artifactPrefix, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw 'Plan, preregistration, and execution-binding paths must remain beneath the artifact root'
    }
}
$planFileName = [System.IO.Path]::GetFileName($planResolved)
$preregistrationFileName = [System.IO.Path]::GetFileName($preregistrationResolved)
$executionBindingFileName = [System.IO.Path]::GetFileName($executionBindingResolved)

$imageId = (& docker image inspect --format '{{.Id}}' $Image).Trim()
if ($LASTEXITCODE -ne 0 -or $imageId -notmatch '^sha256:[0-9a-f]{64}$') {
    throw 'Image must resolve to one exact local sha256 image ID'
}
$plan = Get-Content -Raw -LiteralPath $planResolved | ConvertFrom-Json
$preregistration = Get-Content -Raw -LiteralPath $preregistrationResolved | ConvertFrom-Json
$executionBinding = Get-Content -Raw -LiteralPath $executionBindingResolved | ConvertFrom-Json
if ($plan.runtime_image_id -ne $imageId) {
    throw 'Resolved image ID differs from the exact image sealed in the reviewed plan'
}
if ([string]$plan.plan_sha256 -cne $ReviewedPlanSha256) {
    throw 'ReviewedPlanSha256 differs from the exact sealed plan'
}
if ([string]$preregistration.preregistration_sha256 -cne $ReviewedPreregistrationSha256) {
    throw 'ReviewedPreregistrationSha256 differs from the exact sealed preregistration'
}
if ([string]$executionBinding.execution_binding_sha256 -cne $ReviewedExecutionBindingSha256) {
    throw 'ReviewedExecutionBindingSha256 differs from the exact sealed execution binding'
}
if ([string]$executionBinding.preregistration_sha256 -cne $ReviewedPreregistrationSha256) {
    throw 'Execution binding does not bind the reviewed preregistration'
}
if ([string]$executionBinding.plan_sha256 -cne $ReviewedPlanSha256) {
    throw 'Execution binding does not bind the reviewed plan'
}
if ([string]$plan.selected_case_set_sha256 -cne $ReviewedCaseSetSha256) {
    throw 'ReviewedCaseSetSha256 differs from the exact sealed plan'
}
if ([string]$plan.budget_cap_usd -cne $ApprovedBudgetCapUsd) {
    throw 'ApprovedBudgetCapUsd differs from the exact sealed plan'
}
if ($CanaryAcknowledgement -cne 'I authorize this exact sealed 25-case LLF semantic paid Luna canary plan.') {
    throw 'CanaryAcknowledgement must exactly confirm this reviewed canary plan'
}
if ($plan.purpose -cne 'development_llf_canary_25' -or $plan.cases.Count -ne 25) {
    throw 'The reviewed plan is not the exact 25-case LLF development canary'
}

$hostOutputPath = Join-Path $artifactRootResolved $RunId
[System.IO.Directory]::CreateDirectory($hostOutputPath) | Out-Null
$hostOutputResolved = (Resolve-Path -LiteralPath $hostOutputPath -ErrorAction Stop).Path
$hostRunDirectorySha256 = Get-NormalizedHostPathSha256 -Path $hostOutputResolved
$authorizationStatePath = Join-Path $artifactRootResolved '.real-live-authorization-state'
[System.IO.Directory]::CreateDirectory($authorizationStatePath) | Out-Null
$authorizationStateResolved = (Resolve-Path -LiteralPath $authorizationStatePath -ErrorAction Stop).Path
$authorizationStateDirectorySha256 = Get-NormalizedHostPathSha256 -Path $authorizationStateResolved
if ([string]$executionBinding.runtime_image_id -cne $imageId) {
    throw 'Execution binding image ID differs from the exact reviewed image'
}
if ([string]$executionBinding.intended_run_id -cne $RunId -or [string]$executionBinding.intended_authorization_id -cne $AuthorizationId) {
    throw 'Execution binding run or authorization ID differs from this authorization'
}
if ([string]$executionBinding.runtime_output_directory -cne '/run/artifacts/output' -or [string]$executionBinding.runtime_output_directory_sha256 -cne 'dcab705d1852d51d312812cbcbababa287f19891c9bc3c5e84f049299f05329e') {
    throw 'Execution binding runtime output scope is not the fixed container path'
}
if ([string]$executionBinding.host_output_directory_sha256 -cne $hostRunDirectorySha256) {
    throw 'Execution binding host output path differs from this authorization'
}
if ([string]$executionBinding.authorization_state_directory_sha256 -cne $authorizationStateDirectorySha256) {
    throw 'Execution binding durable state path differs from this authorization'
}
$dockerArgs = @(
    'run', '--rm', '--read-only', '--cap-drop=ALL',
    '--security-opt=no-new-privileges', '--pids-limit=128',
    '--tmpfs=/tmp:rw,noexec,nosuid,nodev,size=16m', '--network=none',
    '--mount', "type=bind,src=$artifactRootResolved,dst=/run/artifacts",
    '--entrypoint', '/opt/venv/bin/criteriabench-real-live',
    $imageId,
    'authorize',
    '--artifact-root', '/run/artifacts',
    '--plan', $planFileName,
    '--preregistration', $preregistrationFileName,
    '--execution-binding', $executionBindingFileName,
    '--output', $AuthorizationFileName,
    '--authorization-id', $AuthorizationId,
    '--authorized-at-utc', $AuthorizedAtUtc,
    '--run-id', $RunId,
    '--runtime-output-path', '/run/artifacts/output',
    '--host-run-directory-sha256', $hostRunDirectorySha256,
    '--authorization-state-directory-sha256', $authorizationStateDirectorySha256,
    '--acknowledge-llf-canary'
)
& docker @dockerArgs
if ($LASTEXITCODE -ne 0) {
    throw "Offline authorization container exited with code $LASTEXITCODE"
}

$authorizationPath = Join-Path $artifactRootResolved $AuthorizationFileName
$authorization = Get-Content -Raw -LiteralPath $authorizationPath | ConvertFrom-Json
Write-Output "Authorization path: $authorizationPath"
Write-Output "Authorization SHA256: $($authorization.authorization_sha256)"
Write-Output "Authorized plan SHA256: $($authorization.plan_sha256)"
if ($authorization.host_run_directory_sha256 -cne $hostRunDirectorySha256) {
    throw 'Sealed authorization host path hash differs from the reviewed host output path'
}
Write-Output "Host run directory SHA256: $hostRunDirectorySha256"
Write-Output "Authorization state directory SHA256: $authorizationStateDirectorySha256"
Write-Output "Durable authorization state: $authorizationStatePath"
Write-Output "Run ID: $($authorization.run_id)"
