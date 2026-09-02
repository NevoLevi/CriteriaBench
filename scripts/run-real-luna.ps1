[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$ArtifactRoot,

    [Parameter(Mandatory = $true)]
    [string]$GenerationRoot,

    [Parameter(Mandatory = $true)]
    [string]$PlanPath,

    [Parameter(Mandatory = $true)]
    [string]$AuthorizationPath,

    [Parameter(Mandatory = $true)]
    [string]$PreregistrationPath,

    [Parameter(Mandatory = $true)]
    [string]$ExecutionBindingPath,

    [Parameter(Mandatory = $true)]
    [string]$OutputDirectory,

    [Parameter(Mandatory = $true)]
    [ValidatePattern('^[A-Za-z0-9]+(?:[._-][A-Za-z0-9]+)*$')]
    [string]$RunId,

    [Parameter(Mandatory = $true)]
    [string]$Image,

    [System.Security.SecureString]$ApiKey
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

function Assert-ContainedPath {
    param([string]$Path, [string]$Root, [string]$Label)
    $prefix = $Root.TrimEnd([System.IO.Path]::DirectorySeparatorChar) + [System.IO.Path]::DirectorySeparatorChar
    if (-not $Path.StartsWith($prefix, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "$Label must remain beneath the explicit artifact root"
    }
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

$artifactRootResolved = (Resolve-Path -LiteralPath $ArtifactRoot -ErrorAction Stop).Path
if (-not [System.IO.Directory]::Exists($artifactRootResolved)) {
    throw 'ArtifactRoot must be an existing directory'
}
$generationRootResolved = (Resolve-Path -LiteralPath $GenerationRoot -ErrorAction Stop).Path
if (-not [System.IO.Directory]::Exists($generationRootResolved)) {
    throw 'GenerationRoot must be an existing directory'
}

$planResolved = Resolve-ExistingFile -Path $PlanPath -Label 'PlanPath'
$authorizationResolved = Resolve-ExistingFile -Path $AuthorizationPath -Label 'AuthorizationPath'
$preregistrationResolved = Resolve-ExistingFile -Path $PreregistrationPath -Label 'PreregistrationPath'
$executionBindingResolved = Resolve-ExistingFile -Path $ExecutionBindingPath -Label 'ExecutionBindingPath'
Assert-ContainedPath -Path $planResolved -Root $artifactRootResolved -Label 'PlanPath'
Assert-ContainedPath -Path $authorizationResolved -Root $artifactRootResolved -Label 'AuthorizationPath'
Assert-ContainedPath -Path $preregistrationResolved -Root $artifactRootResolved -Label 'PreregistrationPath'
Assert-ContainedPath -Path $executionBindingResolved -Root $artifactRootResolved -Label 'ExecutionBindingPath'

$outputResolved = [System.IO.Path]::GetFullPath($OutputDirectory)
Assert-ContainedPath -Path $outputResolved -Root $artifactRootResolved -Label 'OutputDirectory'
if ([System.IO.Path]::GetFileName($outputResolved) -ne $RunId) {
    throw 'OutputDirectory leaf name must exactly equal RunId'
}
[System.IO.Directory]::CreateDirectory($outputResolved) | Out-Null
$outputResolved = (Resolve-Path -LiteralPath $outputResolved -ErrorAction Stop).Path
Assert-ContainedPath -Path $outputResolved -Root $artifactRootResolved -Label 'OutputDirectory'
$hostRunDirectorySha256 = Get-NormalizedHostPathSha256 -Path $outputResolved
$authorizationStateResolved = (Resolve-Path -LiteralPath (Join-Path $artifactRootResolved '.real-live-authorization-state') -ErrorAction Stop).Path
if (-not [System.IO.Directory]::Exists($authorizationStateResolved)) {
    throw 'Durable authorization state directory must already exist'
}
$authorizationStateDirectorySha256 = Get-NormalizedHostPathSha256 -Path $authorizationStateResolved

$generationManifest = Resolve-ExistingFile -Path (Join-Path $generationRootResolved 'generation_manifest.json') -Label 'generation_manifest.json'
$generationCases = Resolve-ExistingFile -Path (Join-Path $generationRootResolved 'generation_cases.jsonl') -Label 'generation_cases.jsonl'
$splitAssignments = Resolve-ExistingFile -Path (Join-Path $generationRootResolved 'split_assignments.json') -Label 'split_assignments.json'

$imageId = (& docker image inspect --format '{{.Id}}' $Image).Trim()
if ($LASTEXITCODE -ne 0 -or $imageId -notmatch '^sha256:[0-9a-f]{64}$') {
    throw 'Image must resolve to one exact local sha256 image ID'
}
$sealedPlan = Get-Content -Raw -LiteralPath $planResolved | ConvertFrom-Json
if ($sealedPlan.runtime_image_id -ne $imageId) {
    throw 'Resolved image ID differs from the exact image sealed in the plan'
}
$sealedAuthorization = Get-Content -Raw -LiteralPath $authorizationResolved | ConvertFrom-Json
$sealedPreregistration = Get-Content -Raw -LiteralPath $preregistrationResolved | ConvertFrom-Json
$sealedExecutionBinding = Get-Content -Raw -LiteralPath $executionBindingResolved | ConvertFrom-Json
if ($sealedAuthorization.host_run_directory_sha256 -cne $hostRunDirectorySha256) {
    throw 'Authorization is bound to a different normalized host output directory'
}
if ($sealedAuthorization.authorization_state_directory_sha256 -cne $authorizationStateDirectorySha256) {
    throw 'Authorization is bound to a different durable authorization state directory'
}
if ($sealedAuthorization.preregistration_sha256 -cne $sealedPreregistration.preregistration_sha256) {
    throw 'Authorization and preregistration semantic hashes differ'
}
if ($sealedAuthorization.execution_binding_sha256 -cne $sealedExecutionBinding.execution_binding_sha256) {
    throw 'Authorization and one-shot execution binding hashes differ'
}
if ($sealedExecutionBinding.plan_sha256 -cne $sealedPlan.plan_sha256 -or $sealedExecutionBinding.runtime_image_id -cne $imageId) {
    throw 'Execution binding differs from the sealed plan or exact runtime image'
}
if ($sealedExecutionBinding.intended_run_id -cne $RunId -or $sealedExecutionBinding.intended_authorization_id -cne $sealedAuthorization.authorization_id) {
    throw 'Execution binding differs from the intended run or authorization ID'
}

$commonDockerOptions = @(
    '--rm', '--read-only', '--cap-drop=ALL',
    '--security-opt=no-new-privileges', '--pids-limit=128',
    '--tmpfs=/tmp:rw,noexec,nosuid,nodev,size=16m',
    '--mount', "type=bind,src=$planResolved,dst=/run/artifacts/plan.json,readonly",
    '--mount', "type=bind,src=$authorizationResolved,dst=/run/artifacts/authorization.json,readonly",
    '--mount', "type=bind,src=$preregistrationResolved,dst=/run/artifacts/preregistration.json,readonly",
    '--mount', "type=bind,src=$executionBindingResolved,dst=/run/artifacts/execution-binding.json,readonly",
    '--mount', "type=bind,src=$outputResolved,dst=/run/artifacts/output",
    '--mount', "type=bind,src=$authorizationStateResolved,dst=/run/authorization-state",
    '--mount', "type=bind,src=$generationManifest,dst=/run/generation/generation_manifest.json,readonly",
    '--mount', "type=bind,src=$generationCases,dst=/run/generation/generation_cases.jsonl,readonly",
    '--mount', "type=bind,src=$splitAssignments,dst=/run/generation/split_assignments.json,readonly"
)
$sealedRunArguments = @(
    '--artifact-root', '/run/artifacts',
    '--plan', 'plan.json',
    '--authorization', 'authorization.json',
    '--preregistration', 'preregistration.json',
    '--execution-binding', 'execution-binding.json',
    '--generation-root', '/run/generation',
    '--output-dir', 'output',
    '--authorization-state-dir', '/run/authorization-state',
    '--host-run-directory-sha256', $hostRunDirectorySha256,
    '--authorization-state-directory-sha256', $authorizationStateDirectorySha256,
    '--run-id', $RunId,
    '--runtime-image-id', $imageId
)

$recoveryOutput = & docker run @commonDockerOptions --network=none --entrypoint '/opt/venv/bin/criteriabench-real-live' $imageId recover @sealedRunArguments
$recoveryExitCode = $LASTEXITCODE
$recoveryOutput | Write-Output
if ($recoveryExitCode -ne 0) {
    throw "Offline recovery/preflight container exited with code $recoveryExitCode"
}
$recoveryStatus = $recoveryOutput | Where-Object { $_ -match '^recovery_remaining=[0-9]+$' } | Select-Object -Last 1
if ($null -eq $recoveryStatus) {
    throw 'Offline recovery did not return an exact remaining-case count'
}
$remainingCaseCount = [int]($recoveryStatus -replace '^recovery_remaining=', '')
if ($remainingCaseCount -eq 0) {
    Write-Output 'The sealed run is already terminal; no API key was requested.'
    return
}

if ($null -eq $ApiKey) {
    $ApiKey = Read-Host -Prompt 'OpenAI API key (input hidden)' -AsSecureString
}
if ($ApiKey.Length -le 0) {
    throw 'A non-empty API key is required'
}

$bstr = [System.IntPtr]::Zero
$plainKey = $null
try {
    $bstr = [System.Runtime.InteropServices.Marshal]::SecureStringToBSTR($ApiKey)
    $plainKey = [System.Runtime.InteropServices.Marshal]::PtrToStringBSTR($bstr)
    if ([string]::IsNullOrWhiteSpace($plainKey)) {
        throw 'A non-empty API key is required'
    }

    $dockerArgs = @(
        'run', '--interactive',
        $commonDockerOptions,
        '--network=bridge',
        '--entrypoint', '/opt/venv/bin/criteriabench-real-live',
        $imageId,
        'run',
        $sealedRunArguments,
        '--live',
        '--acknowledge-paid-api',
        '--api-key-stdin'
    )

    $plainKey | & docker @dockerArgs
    if ($LASTEXITCODE -ne 0) {
        throw "Guarded Luna container exited with code $LASTEXITCODE"
    }
}
finally {
    $plainKey = $null
    if ($bstr -ne [System.IntPtr]::Zero) {
        [System.Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr)
    }
}
