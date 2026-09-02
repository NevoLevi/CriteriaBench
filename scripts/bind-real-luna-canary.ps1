[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)] [string]$ArtifactRoot,
    [Parameter(Mandatory = $true)] [string]$Image,
    [Parameter(Mandatory = $true)] [string]$PreregistrationPath,
    [Parameter(Mandatory = $true)] [string]$PlanPath,
    [Parameter(Mandatory = $true)]
    [ValidatePattern('^[A-Za-z0-9]+(?:[._-][A-Za-z0-9]+)*$')]
    [string]$RunId,
    [Parameter(Mandatory = $true)]
    [ValidatePattern('^[A-Za-z0-9]+(?:[._-][A-Za-z0-9]+)*$')]
    [string]$AuthorizationId,
    [Parameter(Mandatory = $true)]
    [ValidatePattern('^[0-9a-f]{64}$')]
    [string]$ReviewedPreregistrationSha256,
    [Parameter(Mandatory = $true)]
    [ValidatePattern('^[0-9a-f]{64}$')]
    [string]$ReviewedPlanSha256,
    [string]$ExecutionBindingFileName = ''
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

if ([string]::IsNullOrWhiteSpace($ExecutionBindingFileName)) {
    $ExecutionBindingFileName = "$RunId-execution-binding.json"
}
if ([System.IO.Path]::GetFileName($ExecutionBindingFileName) -ne $ExecutionBindingFileName) {
    throw 'ExecutionBindingFileName must be a direct filename'
}

$artifactRootResolved = (Resolve-Path -LiteralPath $ArtifactRoot -ErrorAction Stop).Path
$preregistrationResolved = (Resolve-Path -LiteralPath $PreregistrationPath -ErrorAction Stop).Path
$planResolved = (Resolve-Path -LiteralPath $PlanPath -ErrorAction Stop).Path
$preregistration = Get-Content -Raw -LiteralPath $preregistrationResolved | ConvertFrom-Json
$plan = Get-Content -Raw -LiteralPath $planResolved | ConvertFrom-Json
if ([string]$preregistration.preregistration_sha256 -cne $ReviewedPreregistrationSha256) {
    throw 'ReviewedPreregistrationSha256 differs from the exact public preregistration'
}
if ([string]$plan.plan_sha256 -cne $ReviewedPlanSha256) {
    throw 'ReviewedPlanSha256 differs from the exact sealed plan'
}

$imageId = (& docker image inspect --format '{{.Id}}' $Image).Trim()
if ($LASTEXITCODE -ne 0 -or $imageId -notmatch '^sha256:[0-9a-f]{64}$') {
    throw 'Image must resolve to one exact local sha256 image ID'
}
if ([string]$plan.runtime_image_id -cne $imageId) {
    throw 'Resolved image ID differs from the exact image sealed in the reviewed plan'
}

$hostOutputPath = Join-Path $artifactRootResolved $RunId
[System.IO.Directory]::CreateDirectory($hostOutputPath) | Out-Null
$hostOutputResolved = (Resolve-Path -LiteralPath $hostOutputPath -ErrorAction Stop).Path
$hostOutputDirectorySha256 = Get-NormalizedHostPathSha256 -Path $hostOutputResolved
$authorizationStatePath = Join-Path $artifactRootResolved '.real-live-authorization-state'
[System.IO.Directory]::CreateDirectory($authorizationStatePath) | Out-Null
$authorizationStateResolved = (Resolve-Path -LiteralPath $authorizationStatePath -ErrorAction Stop).Path
$authorizationStateDirectorySha256 = Get-NormalizedHostPathSha256 -Path $authorizationStateResolved

$dockerArgs = @(
    'run', '--rm', '--read-only', '--cap-drop=ALL',
    '--security-opt=no-new-privileges', '--pids-limit=128',
    '--tmpfs=/tmp:rw,noexec,nosuid,nodev,size=16m', '--network=none',
    '--mount', "type=bind,src=$artifactRootResolved,dst=/run/artifacts",
    '--mount', "type=bind,src=$preregistrationResolved,dst=/run/bind/preregistration.json,readonly",
    '--mount', "type=bind,src=$planResolved,dst=/run/bind/plan.json,readonly",
    '--entrypoint', '/opt/venv/bin/criteriabench-llf-canary-preregister',
    $imageId,
    'bind-execution',
    '--preregistration', '/run/bind/preregistration.json',
    '--plan', '/run/bind/plan.json',
    '--intended-run-id', $RunId,
    '--intended-authorization-id', $AuthorizationId,
    '--host-output-directory-sha256', $hostOutputDirectorySha256,
    '--authorization-state-directory-sha256', $authorizationStateDirectorySha256,
    '--output', "/run/artifacts/$ExecutionBindingFileName"
)
& docker @dockerArgs
if ($LASTEXITCODE -ne 0) {
    throw "Offline execution-binding container exited with code $LASTEXITCODE"
}

$bindingPath = Join-Path $artifactRootResolved $ExecutionBindingFileName
$binding = Get-Content -Raw -LiteralPath $bindingPath | ConvertFrom-Json
if ([string]$binding.preregistration_sha256 -cne $ReviewedPreregistrationSha256 -or [string]$binding.plan_sha256 -cne $ReviewedPlanSha256) {
    throw 'Created execution binding differs from the reviewed preregistration or plan'
}
if ([string]$binding.intended_run_id -cne $RunId -or [string]$binding.intended_authorization_id -cne $AuthorizationId) {
    throw 'Created execution binding differs from the intended run or authorization ID'
}
if ([string]$binding.host_output_directory_sha256 -cne $hostOutputDirectorySha256 -or [string]$binding.authorization_state_directory_sha256 -cne $authorizationStateDirectorySha256) {
    throw 'Created execution binding differs from the intended host path scopes'
}
Write-Output 'Execution binding created only; no authorization was created.'
Write-Output "Execution binding path: $bindingPath"
Write-Output "Execution binding SHA256: $($binding.execution_binding_sha256)"
Write-Output "Preregistration SHA256: $($binding.preregistration_sha256)"
Write-Output "Plan SHA256: $($binding.plan_sha256)"
Write-Output "Runtime image ID: $($binding.runtime_image_id)"
Write-Output "Host output directory SHA256: $hostOutputDirectorySha256"
Write-Output "Authorization state directory SHA256: $authorizationStateDirectorySha256"
Write-Output 'Review this exact one-shot binding before running the separate authorization script.'
