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

    $command = Get-Command $Name -ErrorAction SilentlyContinue
    if ($command) {
        return $command.Source
    }

    if ($Optional) {
        return $null
    }
    throw "Required command '$Name' was not found in CriteriaBench/.tools or on PATH."
}
