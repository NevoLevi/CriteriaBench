Set-StrictMode -Version Latest

function Resolve-CriteriaBenchTool {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)][string]$ProjectRoot,
        [Parameter(Mandatory)][ValidateSet("az", "docker", "helm", "kind", "kubectl", "kubelogin", "terraform")][string]$Name,
        [switch]$Optional
    )

    $projectCandidates = switch ($Name) {
        "az" { @((Join-Path $ProjectRoot ".tools\azure-cli\bin\az.cmd")) }
        "helm" { @((Join-Path $ProjectRoot ".tools\helm\helm.exe")) }
        "kind" { @((Join-Path $ProjectRoot ".tools\kind\kind.exe")) }
        "kubectl" { @((Join-Path $ProjectRoot ".tools\kubectl\kubectl.exe")) }
        "kubelogin" { @((Join-Path $ProjectRoot ".tools\kubelogin\kubelogin.exe")) }
        "terraform" { @((Join-Path $ProjectRoot ".tools\terraform\terraform.exe")) }
        default { @() }
    }

    foreach ($candidate in $projectCandidates) {
        if (Test-Path -LiteralPath $candidate -PathType Leaf) {
            return (Resolve-Path -LiteralPath $candidate).Path
        }
    }

    $command = Get-Command $Name -CommandType Application -ErrorAction SilentlyContinue |
        Select-Object -First 1
    if ($command) {
        return $command.Source
    }

    if ($Optional) {
        return $null
    }
    throw "Required command '$Name' was not found in CriteriaBench/.tools or on PATH."
}

function Invoke-CriteriaBenchWithAzureCliOnPath {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)][string]$AzPath,
        [Parameter(Mandatory)][scriptblock]$Action
    )

    if (-not (Test-Path -LiteralPath $AzPath -PathType Leaf)) {
        throw "The resolved Azure CLI launcher does not exist."
    }
    $resolvedAz = (Resolve-Path -LiteralPath $AzPath).Path
    $launcherName = [System.IO.Path]::GetFileName($resolvedAz).ToLowerInvariant()
    if ($launcherName -notin @("az", "az.cmd", "az.exe", "az.bat")) {
        throw "The Azure CLI launcher must be az, az.cmd, az.exe, or az.bat."
    }

    $azDirectory = [System.IO.Path]::GetDirectoryName($resolvedAz)
    if ([string]::IsNullOrWhiteSpace($azDirectory) -or
        $azDirectory.Contains([string][System.IO.Path]::PathSeparator)) {
        throw "The Azure CLI directory cannot be safely added to PATH."
    }

    $previousPath = [Environment]::GetEnvironmentVariable("PATH", "Process")
    $separator = [string][System.IO.Path]::PathSeparator
    $scopedPath = if ([string]::IsNullOrEmpty($previousPath)) {
        $azDirectory
    }
    else {
        $azDirectory + $separator + $previousPath
    }

    try {
        [Environment]::SetEnvironmentVariable("PATH", $scopedPath, "Process")
        $visibleAz = Get-Command az -CommandType Application -ErrorAction SilentlyContinue |
            Select-Object -First 1
        if ($null -eq $visibleAz) {
            throw "Azure CLI is not executable by name after its trusted directory was added to PATH."
        }

        $visiblePath = [System.IO.Path]::GetFullPath($visibleAz.Source)
        $expectedPath = [System.IO.Path]::GetFullPath($resolvedAz)
        $comparison = if ([Environment]::OSVersion.Platform -eq [PlatformID]::Win32NT) {
            [StringComparison]::OrdinalIgnoreCase
        }
        else {
            [StringComparison]::Ordinal
        }
        if (-not [string]::Equals($visiblePath, $expectedPath, $comparison)) {
            throw "Azure CLI name resolution did not select the trusted launcher."
        }

        & $Action
    }
    finally {
        [Environment]::SetEnvironmentVariable("PATH", $previousPath, "Process")
    }
}

function Invoke-CriteriaBenchWithKubeloginOnPath {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)][string]$KubeloginPath,
        [Parameter(Mandatory)][scriptblock]$Action
    )

    if (-not (Test-Path -LiteralPath $KubeloginPath -PathType Leaf)) {
        throw "The resolved kubelogin launcher does not exist."
    }
    $resolvedKubelogin = (Resolve-Path -LiteralPath $KubeloginPath).Path
    $launcherName = [System.IO.Path]::GetFileName($resolvedKubelogin).ToLowerInvariant()
    if ($launcherName -notin @("kubelogin", "kubelogin.cmd", "kubelogin.exe", "kubelogin.bat")) {
        throw "The kubelogin launcher must be kubelogin, kubelogin.cmd, kubelogin.exe, or kubelogin.bat."
    }

    $kubeloginDirectory = [System.IO.Path]::GetDirectoryName($resolvedKubelogin)
    if ([string]::IsNullOrWhiteSpace($kubeloginDirectory) -or
        $kubeloginDirectory.Contains([string][System.IO.Path]::PathSeparator)) {
        throw "The kubelogin directory cannot be safely added to PATH."
    }

    $previousPath = [Environment]::GetEnvironmentVariable("PATH", "Process")
    $separator = [string][System.IO.Path]::PathSeparator
    $scopedPath = if ([string]::IsNullOrEmpty($previousPath)) {
        $kubeloginDirectory
    }
    else {
        $kubeloginDirectory + $separator + $previousPath
    }

    try {
        [Environment]::SetEnvironmentVariable("PATH", $scopedPath, "Process")
        $visibleKubelogin = Get-Command kubelogin -CommandType Application -ErrorAction SilentlyContinue |
            Select-Object -First 1
        if ($null -eq $visibleKubelogin) {
            throw "kubelogin is not executable by name after its trusted directory was added to PATH."
        }

        $visiblePath = [System.IO.Path]::GetFullPath($visibleKubelogin.Source)
        $expectedPath = [System.IO.Path]::GetFullPath($resolvedKubelogin)
        $comparison = if ([Environment]::OSVersion.Platform -eq [PlatformID]::Win32NT) {
            [StringComparison]::OrdinalIgnoreCase
        }
        else {
            [StringComparison]::Ordinal
        }
        if (-not [string]::Equals($visiblePath, $expectedPath, $comparison)) {
            throw "kubelogin name resolution did not select the trusted launcher."
        }

        & $Action
    }
    finally {
        [Environment]::SetEnvironmentVariable("PATH", $previousPath, "Process")
    }
}
