[CmdletBinding()]
param(
    [ValidateSet("criteriabench")][string]$ClusterName = "criteriabench",
    [Parameter(Mandatory)][ValidateSet("DELETE-KIND")][string]$Confirmation
)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
. (Join-Path $PSScriptRoot "tooling.ps1")
. (Join-Path $PSScriptRoot "azure-safety.ps1")

$kind = Resolve-CriteriaBenchTool -ProjectRoot $projectRoot -Name kind
$kubeconfigPath = Join-Path $projectRoot ".tools\kind\criteriabench.kubeconfig"
$existingClusters = @(& $kind get clusters)
if ($LASTEXITCODE -ne 0) {
    throw "kind could not list local clusters."
}
if ($existingClusters -contains $ClusterName) {
    Invoke-CriteriaBenchNative -FilePath $kind -ArgumentList @(
        "delete", "cluster", "--name", $ClusterName
    ) -FailureMessage "kind could not delete the exact CriteriaBench cluster"
    if (Test-Path -LiteralPath $kubeconfigPath -PathType Leaf) {
        Remove-Item -LiteralPath $kubeconfigPath -Force
    }
    Write-Host "Deleted local kind cluster '$ClusterName'. Its disposable in-cluster data is not recoverable."
}
else {
    Write-Host "Local kind cluster '$ClusterName' does not exist."
}
