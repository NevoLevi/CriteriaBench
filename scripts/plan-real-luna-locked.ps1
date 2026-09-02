[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)] [string]$ArtifactRoot,
    [Parameter(Mandatory = $true)] [string]$GenerationRoot,
    [Parameter(Mandatory = $true)] [string]$Image,
    [Parameter(Mandatory = $true)] [string]$CreatedAtUtc,
    [Parameter(Mandatory = $true)] [string]$PreregistrationPath,
    [Parameter(Mandatory = $true)] [string]$ExecutionBindingPath,
    [Parameter(Mandatory = $true)] [string]$CanaryPlanPath,
    [Parameter(Mandatory = $true)] [string]$CanaryAuthorizationPath,
    [Parameter(Mandatory = $true)] [string]$ScoreReportPath,
    [Parameter(Mandatory = $true)] [string]$AdvancementDecisionPath,
    [string]$LockedPlanFileName = 'llf-locked-plan.json'
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

function Get-ContainedRelativePath {
    param([string]$Path, [string]$Root, [string]$Label)
    $prefix = $Root.TrimEnd([System.IO.Path]::DirectorySeparatorChar) + [System.IO.Path]::DirectorySeparatorChar
    if (-not $Path.StartsWith($prefix, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "$Label must remain beneath the explicit artifact root"
    }
    return [System.IO.Path]::GetRelativePath($Root, $Path).Replace('\', '/')
}

if ([System.IO.Path]::GetFileName($LockedPlanFileName) -ne $LockedPlanFileName) {
    throw 'LockedPlanFileName must be a direct filename'
}
$artifactRootResolved = (Resolve-Path -LiteralPath $ArtifactRoot -ErrorAction Stop).Path
$generationRootResolved = (Resolve-Path -LiteralPath $GenerationRoot -ErrorAction Stop).Path
$generationManifest = Resolve-ExistingFile -Path (Join-Path $generationRootResolved 'generation_manifest.json') -Label 'generation_manifest.json'
$generationCases = Resolve-ExistingFile -Path (Join-Path $generationRootResolved 'generation_cases.jsonl') -Label 'generation_cases.jsonl'
$splitAssignments = Resolve-ExistingFile -Path (Join-Path $generationRootResolved 'split_assignments.json') -Label 'split_assignments.json'

$preregistrationResolved = Resolve-ExistingFile -Path $PreregistrationPath -Label 'PreregistrationPath'
$executionBindingResolved = Resolve-ExistingFile -Path $ExecutionBindingPath -Label 'ExecutionBindingPath'
$canaryPlanResolved = Resolve-ExistingFile -Path $CanaryPlanPath -Label 'CanaryPlanPath'
$canaryAuthorizationResolved = Resolve-ExistingFile -Path $CanaryAuthorizationPath -Label 'CanaryAuthorizationPath'
$scoreReportResolved = Resolve-ExistingFile -Path $ScoreReportPath -Label 'ScoreReportPath'
$advancementDecisionResolved = Resolve-ExistingFile -Path $AdvancementDecisionPath -Label 'AdvancementDecisionPath'

$preregistrationRelative = Get-ContainedRelativePath -Path $preregistrationResolved -Root $artifactRootResolved -Label 'PreregistrationPath'
$executionBindingRelative = Get-ContainedRelativePath -Path $executionBindingResolved -Root $artifactRootResolved -Label 'ExecutionBindingPath'
$canaryPlanRelative = Get-ContainedRelativePath -Path $canaryPlanResolved -Root $artifactRootResolved -Label 'CanaryPlanPath'
$canaryAuthorizationRelative = Get-ContainedRelativePath -Path $canaryAuthorizationResolved -Root $artifactRootResolved -Label 'CanaryAuthorizationPath'
$scoreReportRelative = Get-ContainedRelativePath -Path $scoreReportResolved -Root $artifactRootResolved -Label 'ScoreReportPath'
$advancementDecisionRelative = Get-ContainedRelativePath -Path $advancementDecisionResolved -Root $artifactRootResolved -Label 'AdvancementDecisionPath'

$imageId = (& docker image inspect --format '{{.Id}}' $Image).Trim()
if ($LASTEXITCODE -ne 0 -or $imageId -notmatch '^sha256:[0-9a-f]{64}$') {
    throw 'Image must resolve to one exact local sha256 image ID'
}
$canaryPlan = Get-Content -Raw -LiteralPath $canaryPlanResolved | ConvertFrom-Json
$decision = Get-Content -Raw -LiteralPath $advancementDecisionResolved | ConvertFrom-Json
if ([string]$canaryPlan.runtime_image_id -cne $imageId) {
    throw 'Resolved image ID differs from the exact image sealed in the canary plan'
}
if ($decision.advancement_status -cne 'pass' -or $decision.proceed_to_separate_locked_authorization -ne $true) {
    throw 'Locked planning requires an exact sealed PASS advancement decision'
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
    'plan-locked',
    '--artifact-root', '/run/artifacts',
    '--generation-root', '/run/generation',
    '--created-at-utc', $CreatedAtUtc,
    '--runtime-image-id', $imageId,
    '--preregistration', $preregistrationRelative,
    '--execution-binding', $executionBindingRelative,
    '--canary-plan', $canaryPlanRelative,
    '--canary-authorization', $canaryAuthorizationRelative,
    '--score-report', $scoreReportRelative,
    '--advancement-decision', $advancementDecisionRelative,
    '--output', $LockedPlanFileName
)
& docker @dockerArgs
if ($LASTEXITCODE -ne 0) {
    throw "Offline PASS-gated locked-plan container exited with code $LASTEXITCODE"
}

$lockedPlanPath = Join-Path $artifactRootResolved $LockedPlanFileName
$lockedPlan = Get-Content -Raw -LiteralPath $lockedPlanPath | ConvertFrom-Json
if ($lockedPlan.purpose -cne 'locked_llf_test' -or $lockedPlan.runtime_image_id -cne $imageId) {
    throw 'Created locked plan differs from the verified PASS-gated image and purpose'
}
Write-Output 'Locked plan created only; no locked paid authorization was created.'
Write-Output "Locked plan path: $lockedPlanPath"
Write-Output "Locked plan SHA256: $($lockedPlan.plan_sha256)"
Write-Output "Locked case-set SHA256: $($lockedPlan.selected_case_set_sha256)"
Write-Output "Cases: $($lockedPlan.cases.Count)"
Write-Output "Budget cap USD: $($lockedPlan.budget_cap_usd)"
Write-Output 'Paid locked execution remains structurally disabled until its bounded authorization-window protocol is implemented.'
