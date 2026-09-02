[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)] [string]$ArtifactRoot,
    [Parameter(Mandatory = $true)] [string]$GenerationRoot,
    [Parameter(Mandatory = $true)] [string]$Image,
    [Parameter(Mandatory = $true)] [string]$CreatedAtUtc,
    [string]$PlanFileName = 'llf-canary-plan.json'
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
if ([System.IO.Path]::GetFileName($PlanFileName) -ne $PlanFileName) {
    throw 'PlanFileName must be a direct filename'
}

$artifactRootResolved = (Resolve-Path -LiteralPath $ArtifactRoot -ErrorAction Stop).Path
$generationRootResolved = (Resolve-Path -LiteralPath $GenerationRoot -ErrorAction Stop).Path
$generationManifest = (Resolve-Path -LiteralPath (Join-Path $generationRootResolved 'generation_manifest.json') -ErrorAction Stop).Path
$generationCases = (Resolve-Path -LiteralPath (Join-Path $generationRootResolved 'generation_cases.jsonl') -ErrorAction Stop).Path
$splitAssignments = (Resolve-Path -LiteralPath (Join-Path $generationRootResolved 'split_assignments.json') -ErrorAction Stop).Path
$imageId = (& docker image inspect --format '{{.Id}}' $Image).Trim()
if ($LASTEXITCODE -ne 0 -or $imageId -notmatch '^sha256:[0-9a-f]{64}$') {
    throw 'Image must resolve to one exact local sha256 image ID'
}

$dockerArgs = @(
    'run', '--rm', '--read-only', '--cap-drop=ALL',
    '--security-opt=no-new-privileges', '--pids-limit=128',
    '--tmpfs=/tmp:rw,noexec,nosuid,nodev,size=16m', '--network=none',
    '--mount', "type=bind,src=$artifactRootResolved,dst=/run/artifacts",
    '--mount', "type=bind,src=$generationManifest,dst=/run/generation/generation_manifest.json,readonly",
    '--mount', "type=bind,src=$generationCases,dst=/run/generation/generation_cases.jsonl,readonly",
    '--mount', "type=bind,src=$splitAssignments,dst=/run/generation/split_assignments.json,readonly",
    '--entrypoint', '/opt/venv/bin/criteriabench-real-live',
    $imageId,
    'plan-llf-canary',
    '--artifact-root', '/run/artifacts',
    '--generation-root', '/run/generation',
    '--created-at-utc', $CreatedAtUtc,
    '--runtime-image-id', $imageId,
    '--output', $PlanFileName
)
& docker @dockerArgs
if ($LASTEXITCODE -ne 0) {
    throw "Offline plan container exited with code $LASTEXITCODE"
}

$planPath = Join-Path $artifactRootResolved $PlanFileName
$plan = Get-Content -Raw -LiteralPath $planPath | ConvertFrom-Json
Write-Output 'Plan created only; no authorization was created.'
Write-Output "Plan path: $planPath"
Write-Output "Plan SHA256: $($plan.plan_sha256)"
Write-Output "Selected case-set SHA256: $($plan.selected_case_set_sha256)"
Write-Output "Runtime image ID: $($plan.runtime_image_id)"
Write-Output "Cases: $($plan.cases.Count)"
Write-Output "Budget cap USD: $($plan.budget_cap_usd)"
Write-Output 'Review these exact values before creating the separate one-shot execution binding.'
