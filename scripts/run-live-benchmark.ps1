[CmdletBinding()]
param(
    [Parameter(Mandatory, Position = 0)][string[]]$FixturePath,
    [Parameter(Mandatory)][string]$OutputPath,
    [Parameter(Mandatory)][ValidateRange(0.01, 2.0)][decimal]$BudgetUsd,
    [Parameter(Mandatory)][switch]$Live,
    [Parameter(Mandatory)][switch]$AcknowledgePaidApi,
    [switch]$Overwrite
)

$ErrorActionPreference = "Stop"
if (-not $Live -or -not $AcknowledgePaidApi) {
    throw "A paid run requires both -Live and -AcknowledgePaidApi."
}

$projectRoot = Split-Path -Parent $PSScriptRoot
. (Join-Path $PSScriptRoot "live-benchmark-support.ps1")

function Resolve-CriteriaBenchAbsolutePath {
    param([Parameter(Mandatory)][string]$Path)

    if ([System.IO.Path]::IsPathRooted($Path)) {
        return [System.IO.Path]::GetFullPath($Path)
    }
    return [System.IO.Path]::GetFullPath((Join-Path $projectRoot $Path))
}

function Test-CriteriaBenchChildPath {
    param(
        [Parameter(Mandatory)][string]$Parent,
        [Parameter(Mandatory)][string]$Child
    )

    $parentPrefix = [System.IO.Path]::GetFullPath($Parent).TrimEnd('\', '/') +
        [System.IO.Path]::DirectorySeparatorChar
    $childFull = [System.IO.Path]::GetFullPath($Child)
    return $childFull.StartsWith($parentPrefix, [StringComparison]::OrdinalIgnoreCase)
}

$allowedDataRoots = @(
    (Join-Path $projectRoot "data\public"),
    (Join-Path $projectRoot "data\synthetic")
)
$resolvedFixtures = @()
foreach ($path in $FixturePath) {
    $resolved = Resolve-CriteriaBenchAbsolutePath -Path $path
    $allowed = $false
    foreach ($dataRoot in $allowedDataRoots) {
        if (Test-CriteriaBenchChildPath -Parent $dataRoot -Child $resolved) {
            $allowed = $true
            break
        }
    }
    if (-not $allowed -or -not (Test-Path -LiteralPath $resolved -PathType Leaf)) {
        throw "Every live fixture must be an existing file under data/public or data/synthetic."
    }
    $resolvedFixtures += $resolved
}

$resolvedOutput = Resolve-CriteriaBenchAbsolutePath -Path $OutputPath
$artifactRoot = Join-Path $projectRoot "artifacts"
if (-not (Test-CriteriaBenchChildPath -Parent $artifactRoot -Child $resolvedOutput) -or
    [System.IO.Path]::GetExtension($resolvedOutput) -ne ".json") {
    throw "Live benchmark output must be a JSON file under the project artifacts directory."
}
if ((Test-Path -LiteralPath $resolvedOutput -PathType Leaf) -and -not $Overwrite) {
    throw "The output already exists; pass -Overwrite to replace it explicitly."
}

$uvCandidates = @(
    (Join-Path $projectRoot ".tools\uv\uv.exe"),
    (Join-Path $projectRoot ".tools\uv\uv")
)
$uv = $null
foreach ($candidate in $uvCandidates) {
    if (Test-Path -LiteralPath $candidate -PathType Leaf) {
        $uv = (Resolve-Path -LiteralPath $candidate).Path
        break
    }
}
if ($null -eq $uv) {
    $uvCommand = Get-Command uv -CommandType Application -ErrorAction SilentlyContinue
    if ($uvCommand) {
        $uv = $uvCommand.Source
    }
}
if ($null -eq $uv) {
    throw "Pinned uv 0.12.8 is required in .tools/uv or on PATH."
}
$uvVersion = (& $uv --version 2>$null | Select-Object -First 1)
if ($LASTEXITCODE -ne 0 -or $uvVersion -notmatch '^uv 0\.12\.8(?:\s|$)') {
    throw "The live benchmark requires exactly uv 0.12.8."
}

# All non-secret validation is complete before the ignored file is opened.
$envFilePath = Join-Path $projectRoot ".env.local"
$apiKey = Read-CriteriaBenchOpenAIKey -LiteralPath $envFilePath
$budgetText = $BudgetUsd.ToString([Globalization.CultureInfo]::InvariantCulture)
$benchmarkArgs = @(
    "run", "--frozen", "--no-env-file", "criteriabench-benchmark"
) + $resolvedFixtures + @(
    "--output", $resolvedOutput,
    "--live", "--acknowledge-paid-api", "--budget-usd", $budgetText
)
if ($Overwrite) {
    $benchmarkArgs += "--overwrite"
}

$liveEnvironment = @{
    OPENAI_API_KEY                     = $apiKey
    LLM_PROVIDER                       = "openai"
    ALLOW_PAID_CALLS                   = "true"
    LIVE_RUN_BUDGET_USD                = $budgetText
    OPENAI_MODEL                       = "gpt-5.6-luna"
    PRICING_MODEL                      = "gpt-5.6-luna"
    INPUT_COST_PER_MILLION_USD         = "0.20"
    OUTPUT_COST_PER_MILLION_USD        = "1.20"
    UV_NO_ENV_FILE                     = "1"
    UV_PYTHON_DOWNLOADS                = "never"
}

Push-Location $projectRoot
try {
    Invoke-CriteriaBenchScopedEnvironment -Variables $liveEnvironment -Action {
        & $uv @benchmarkArgs
        if ($LASTEXITCODE -ne 0) {
            throw "The guarded live benchmark failed with exit code $LASTEXITCODE."
        }
    }
}
finally {
    Pop-Location
    $liveEnvironment.Clear()
    $apiKey = $null
}
