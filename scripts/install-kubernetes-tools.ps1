[CmdletBinding()]
param(
    [ValidatePattern("^v[0-9]+\.[0-9]+\.[0-9]+$")][string]$KubectlVersion = "v1.36.4",
    [ValidatePattern("^v[0-9]+\.[0-9]+\.[0-9]+$")][string]$KubeloginVersion = "v0.2.19"
)

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"
$projectRoot = Split-Path -Parent $PSScriptRoot
$toolsRoot = Join-Path $projectRoot ".tools"
$downloadRoot = Join-Path $toolsRoot (".downloads\" + [Guid]::NewGuid().ToString("N"))

$resolvedProjectRoot = (Resolve-Path -LiteralPath $projectRoot).Path
New-Item -ItemType Directory -Force -Path $downloadRoot | Out-Null
$resolvedDownloadRoot = (Resolve-Path -LiteralPath $downloadRoot).Path
if (-not $resolvedDownloadRoot.StartsWith($resolvedProjectRoot + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)) {
    throw "Refusing to use a download directory outside CriteriaBench."
}

function Get-ExpectedSha256 {
    [CmdletBinding()]
    param([Parameter(Mandatory)][string]$ChecksumPath)

    $checksumText = Get-Content -Raw -LiteralPath $ChecksumPath
    $match = [regex]::Match($checksumText, "(?i)\b[a-f0-9]{64}\b")
    if (-not $match.Success) {
        throw "The official checksum file did not contain a SHA-256 digest."
    }
    return $match.Value.ToLowerInvariant()
}

function Assert-FileSha256 {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)][string]$FilePath,
        [Parameter(Mandatory)][string]$ChecksumPath
    )

    $expected = Get-ExpectedSha256 -ChecksumPath $ChecksumPath
    $actual = (Get-FileHash -LiteralPath $FilePath -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($actual -ne $expected) {
        throw "SHA-256 verification failed for $(Split-Path -Leaf $FilePath)."
    }
}

try {
    $kubectlUrl = "https://dl.k8s.io/release/$KubectlVersion/bin/windows/amd64/kubectl.exe"
    $kubectlDownload = Join-Path $downloadRoot "kubectl.exe"
    $kubectlChecksum = Join-Path $downloadRoot "kubectl.exe.sha256"
    Invoke-WebRequest -UseBasicParsing -Uri $kubectlUrl -OutFile $kubectlDownload
    Invoke-WebRequest -UseBasicParsing -Uri "$kubectlUrl.sha256" -OutFile $kubectlChecksum
    Assert-FileSha256 -FilePath $kubectlDownload -ChecksumPath $kubectlChecksum

    $kubeloginBaseUrl = "https://github.com/Azure/kubelogin/releases/download/$KubeloginVersion"
    $kubeloginArchive = Join-Path $downloadRoot "kubelogin-win-amd64.zip"
    $kubeloginChecksum = Join-Path $downloadRoot "kubelogin-win-amd64.zip.sha256"
    $kubeloginExtract = Join-Path $downloadRoot "kubelogin"
    Invoke-WebRequest -UseBasicParsing -Uri "$kubeloginBaseUrl/kubelogin-win-amd64.zip" -OutFile $kubeloginArchive
    Invoke-WebRequest -UseBasicParsing -Uri "$kubeloginBaseUrl/kubelogin-win-amd64.zip.sha256" -OutFile $kubeloginChecksum
    Assert-FileSha256 -FilePath $kubeloginArchive -ChecksumPath $kubeloginChecksum
    Expand-Archive -LiteralPath $kubeloginArchive -DestinationPath $kubeloginExtract
    $kubeloginBinary = Get-ChildItem -LiteralPath $kubeloginExtract -Filter kubelogin.exe -File -Recurse | Select-Object -First 1
    if (-not $kubeloginBinary) {
        throw "The verified kubelogin archive did not contain kubelogin.exe."
    }

    $kubectlDirectory = Join-Path $toolsRoot "kubectl"
    $kubeloginDirectory = Join-Path $toolsRoot "kubelogin"
    New-Item -ItemType Directory -Force -Path $kubectlDirectory, $kubeloginDirectory | Out-Null
    Copy-Item -LiteralPath $kubectlDownload -Destination (Join-Path $kubectlDirectory "kubectl.exe") -Force
    Copy-Item -LiteralPath $kubeloginBinary.FullName -Destination (Join-Path $kubeloginDirectory "kubelogin.exe") -Force

    & (Join-Path $kubectlDirectory "kubectl.exe") version --client
    if ($LASTEXITCODE -ne 0) {
        throw "The installed kubectl binary did not start successfully."
    }
    & (Join-Path $kubeloginDirectory "kubelogin.exe") --version
    if ($LASTEXITCODE -ne 0) {
        throw "The installed kubelogin binary did not start successfully."
    }

    Write-Host "Installed verified project-local kubectl $KubectlVersion and kubelogin $KubeloginVersion."
}
finally {
    if (Test-Path -LiteralPath $resolvedDownloadRoot) {
        Remove-Item -LiteralPath $resolvedDownloadRoot -Recurse -Force
    }
}
