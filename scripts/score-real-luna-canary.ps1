[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)] [string]$RunDirectory,
    [Parameter(Mandatory = $true)] [string]$AuthorizationStateDirectory,
    [Parameter(Mandatory = $true)] [string]$PreregistrationPath,
    [Parameter(Mandatory = $true)] [string]$ExecutionBindingPath,
    [Parameter(Mandatory = $true)] [string]$GenerationRoot,
    [Parameter(Mandatory = $true)] [string]$CoverageRoot,
    [Parameter(Mandatory = $true)] [string]$ReportOutputDirectory,
    [Parameter(Mandatory = $true)] [string]$Image,
    [string]$ReportFileName = 'llf-canary-score.json'
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

function Resolve-ExistingFile {
    param([string]$Path, [string]$Label)
    $resolved = (Resolve-Path -LiteralPath $Path -ErrorAction Stop).Path
    if (-not [System.IO.File]::Exists($resolved)) {
        throw "$Label must be an existing regular file"
    }
    $item = Get-Item -LiteralPath $resolved -Force
    if (($item.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
        throw "$Label must not be a symbolic link or reparse point"
    }
    return $resolved
}

function Resolve-ExistingDirectory {
    param([string]$Path, [string]$Label)
    $resolved = (Resolve-Path -LiteralPath $Path -ErrorAction Stop).Path
    if (-not [System.IO.Directory]::Exists($resolved)) {
        throw "$Label must be an existing directory"
    }
    $item = Get-Item -LiteralPath $resolved -Force
    if (($item.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
        throw "$Label must not be a symbolic link or reparse point"
    }
    return $resolved
}

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

function Test-ContainedOrEqual {
    param([string]$Path, [string]$Root)
    $pathWithoutSeparator = $Path.TrimEnd([System.IO.Path]::DirectorySeparatorChar)
    $rootWithoutSeparator = $Root.TrimEnd([System.IO.Path]::DirectorySeparatorChar)
    if ($pathWithoutSeparator.Equals($rootWithoutSeparator, [System.StringComparison]::OrdinalIgnoreCase)) {
        return $true
    }
    $prefix = $rootWithoutSeparator + [System.IO.Path]::DirectorySeparatorChar
    return $pathWithoutSeparator.StartsWith($prefix, [System.StringComparison]::OrdinalIgnoreCase)
}

if ([System.IO.Path]::GetFileName($ReportFileName) -ne $ReportFileName) {
    throw 'ReportFileName must be a direct filename'
}
$sealedRunNames = @(
    '.real-live.lock', 'plan.json', 'authorization.json',
    'authorization-consumed.json', 'summary.json', 'pending.json'
)
if ($sealedRunNames -contains $ReportFileName -or $ReportFileName -match '^(?:attempt|case)-[0-9]{4}\.json$') {
    throw 'ReportFileName must not match any sealed live-run artifact name'
}

$runResolved = Resolve-ExistingDirectory -Path $RunDirectory -Label 'RunDirectory'
$authorizationStateResolved = Resolve-ExistingDirectory -Path $AuthorizationStateDirectory -Label 'AuthorizationStateDirectory'
$generationResolved = Resolve-ExistingDirectory -Path $GenerationRoot -Label 'GenerationRoot'
$coverageResolved = Resolve-ExistingDirectory -Path $CoverageRoot -Label 'CoverageRoot'
$reportResolved = Resolve-ExistingDirectory -Path $ReportOutputDirectory -Label 'ReportOutputDirectory'
if (
    (Test-ContainedOrEqual -Path $reportResolved -Root $runResolved) -or
    (Test-ContainedOrEqual -Path $runResolved -Root $reportResolved) -or
    (Test-ContainedOrEqual -Path $reportResolved -Root $authorizationStateResolved) -or
    (Test-ContainedOrEqual -Path $authorizationStateResolved -Root $reportResolved) -or
    (Test-ContainedOrEqual -Path $reportResolved -Root $generationResolved) -or
    (Test-ContainedOrEqual -Path $generationResolved -Root $reportResolved) -or
    (Test-ContainedOrEqual -Path $reportResolved -Root $coverageResolved) -or
    (Test-ContainedOrEqual -Path $coverageResolved -Root $reportResolved)
) {
    throw 'ReportOutputDirectory must be disjoint from sealed input and state directories'
}

$planPath = Resolve-ExistingFile -Path (Join-Path $runResolved 'plan.json') -Label 'sealed plan.json'
$authorizationPath = Resolve-ExistingFile -Path (Join-Path $runResolved 'authorization.json') -Label 'sealed authorization.json'
$preregistrationResolved = Resolve-ExistingFile -Path $PreregistrationPath -Label 'public preregistration'
$executionBindingResolved = Resolve-ExistingFile -Path $ExecutionBindingPath -Label 'public execution binding'
if (
    (Test-ContainedOrEqual -Path $preregistrationResolved -Root $reportResolved) -or
    (Test-ContainedOrEqual -Path $executionBindingResolved -Root $reportResolved)
) {
    throw 'ReportOutputDirectory must not contain public chain inputs'
}
$generationManifest = Resolve-ExistingFile -Path (Join-Path $generationResolved 'generation_manifest.json') -Label 'generation_manifest.json'
$generationCases = Resolve-ExistingFile -Path (Join-Path $generationResolved 'generation_cases.jsonl') -Label 'generation_cases.jsonl'
$splitAssignments = Resolve-ExistingFile -Path (Join-Path $generationResolved 'split_assignments.json') -Label 'split_assignments.json'
$developmentReferences = Resolve-ExistingFile -Path (Join-Path $generationResolved 'development_references.jsonl') -Label 'development_references.jsonl'
$developmentCoverage = Resolve-ExistingFile -Path (Join-Path $coverageResolved 'llf-semantic-coverage-development.json') -Label 'llf-semantic-coverage-development.json'

$reportPath = Join-Path $reportResolved $ReportFileName
if ([System.IO.File]::Exists($reportPath)) {
    throw 'Refusing to overwrite an existing sealed score report'
}

$imageId = (& docker image inspect --format '{{.Id}}' $Image).Trim()
if ($LASTEXITCODE -ne 0 -or $imageId -notmatch '^sha256:[0-9a-f]{64}$') {
    throw 'Image must resolve to one exact local sha256 image ID'
}
$sealedPlan = Get-Content -Raw -LiteralPath $planPath | ConvertFrom-Json
if ($sealedPlan.runtime_image_id -ne $imageId) {
    throw 'Resolved image ID differs from the exact image sealed in the run plan'
}
$hostRunDirectorySha256 = Get-NormalizedHostPathSha256 -Path $runResolved
$authorizationStateDirectorySha256 = Get-NormalizedHostPathSha256 -Path $authorizationStateResolved
$sealedAuthorization = Get-Content -Raw -LiteralPath $authorizationPath | ConvertFrom-Json
$sealedBinding = Get-Content -Raw -LiteralPath $executionBindingResolved | ConvertFrom-Json
if ([string]$sealedAuthorization.host_run_directory_sha256 -cne $hostRunDirectorySha256) {
    throw 'Authorization is bound to a different normalized host run directory'
}
if ([string]$sealedAuthorization.authorization_state_directory_sha256 -cne $authorizationStateDirectorySha256) {
    throw 'Authorization is bound to a different durable state directory'
}
if ([string]$sealedBinding.host_output_directory_sha256 -cne $hostRunDirectorySha256) {
    throw 'Execution binding is bound to a different normalized host run directory'
}
if ([string]$sealedBinding.authorization_state_directory_sha256 -cne $authorizationStateDirectorySha256) {
    throw 'Execution binding is bound to a different durable state directory'
}
$preregistrationArtifactSha256 = (Get-FileHash -Algorithm SHA256 -LiteralPath $preregistrationResolved).Hash.ToLowerInvariant()
$executionBindingArtifactSha256 = (Get-FileHash -Algorithm SHA256 -LiteralPath $executionBindingResolved).Hash.ToLowerInvariant()
if ([string]$sealedBinding.preregistration_artifact_sha256 -cne $preregistrationArtifactSha256) {
    throw 'Execution binding names different preregistration artifact bytes'
}
if ([string]$sealedAuthorization.execution_binding_artifact_sha256 -cne $executionBindingArtifactSha256) {
    throw 'Authorization names different execution-binding artifact bytes'
}

$dockerArgs = @(
    'run', '--rm', '--read-only', '--cap-drop=ALL',
    '--security-opt=no-new-privileges', '--pids-limit=128',
    '--tmpfs=/tmp:rw,noexec,nosuid,nodev,size=16m', '--network=none',
    '--mount', "type=bind,src=$runResolved,dst=/run/artifacts/output,readonly",
    '--mount', "type=bind,src=$authorizationStateResolved,dst=/run/authorization-state,readonly",
    '--mount', "type=bind,src=$preregistrationResolved,dst=/run/public/llf-canary-preregistration.json,readonly",
    '--mount', "type=bind,src=$executionBindingResolved,dst=/run/public/llf-canary-execution-binding.json,readonly",
    '--mount', "type=bind,src=$generationManifest,dst=/run/dataset/generation_manifest.json,readonly",
    '--mount', "type=bind,src=$generationCases,dst=/run/dataset/generation_cases.jsonl,readonly",
    '--mount', "type=bind,src=$splitAssignments,dst=/run/dataset/split_assignments.json,readonly",
    '--mount', "type=bind,src=$developmentReferences,dst=/run/dataset/development_references.jsonl,readonly",
    '--mount', "type=bind,src=$developmentCoverage,dst=/run/coverage/llf-semantic-coverage-development.json,readonly",
    '--mount', "type=bind,src=$reportResolved,dst=/run/report",
    '--entrypoint', '/opt/venv/bin/python',
    $imageId,
    '-m', 'criteriabench.real_eval.llf_live_score', 'score',
    '--run-dir', '/run/artifacts/output',
    '--authorization-state-dir', '/run/authorization-state',
    '--preregistration', '/run/public/llf-canary-preregistration.json',
    '--execution-binding', '/run/public/llf-canary-execution-binding.json',
    '--host-run-directory-sha256', $hostRunDirectorySha256,
    '--authorization-state-directory-sha256', $authorizationStateDirectorySha256,
    '--dataset-dir', '/run/dataset',
    '--coverage-dir', '/run/coverage',
    '--output', "/run/report/$ReportFileName"
)
& docker @dockerArgs
if ($LASTEXITCODE -ne 0) {
    throw "Offline LLF scoring container exited with code $LASTEXITCODE"
}

$report = Get-Content -Raw -LiteralPath $reportPath | ConvertFrom-Json
Write-Output "Score report path: $reportPath"
Write-Output "Score report SHA256: $($report.report_sha256)"
Write-Output "Primary-structure F1: $($report.metrics.primary_structure.f1)"
Write-Output "Exact-match accuracy: $($report.metrics.exact_match_accuracy)"
