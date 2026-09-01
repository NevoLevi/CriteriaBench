[CmdletBinding()]
param(
    [Parameter(Mandatory)][string]$Subscription,
    [Parameter(Mandatory)][ValidateSet("DESTROY-CRITERIABENCH")][string]$Confirmation
)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
. (Join-Path $PSScriptRoot "tooling.ps1")
. (Join-Path $PSScriptRoot "azure-safety.ps1")

$az = Resolve-CriteriaBenchTool -ProjectRoot $projectRoot -Name az
$terraform = Resolve-CriteriaBenchTool -ProjectRoot $projectRoot -Name terraform
$terraformDirectory = Join-Path $projectRoot "infra\azure"
$kubeconfigPath = Join-Path $terraformDirectory ".kubeconfig"
$planPath = Join-Path $terraformDirectory ".criteriabench.tfplan"
$summaryPath = Join-Path $terraformDirectory ".criteriabench-plan-summary.json"
$null = Assert-CriteriaBenchChildPath -ProjectRoot $projectRoot -TargetPath $terraformDirectory

Invoke-CriteriaBenchNative -FilePath $az -ArgumentList @(
    "account", "set", "--subscription", $Subscription
) -FailureMessage "Unable to select the requested Azure subscription"

Invoke-CriteriaBenchWithAzureCliOnPath -AzPath $az -Action {
    Invoke-CriteriaBenchNative -FilePath $terraform -ArgumentList @(
        "-chdir=$terraformDirectory", "init", "-input=false"
    ) -FailureMessage "Terraform initialization failed"

    $expectedParent = "rg-criteriabench-demo"
    $expectedNode = "rg-criteriabench-aks-nodes-demo"
    $stateParent = & $terraform "-chdir=$terraformDirectory" output -raw resource_group_name 2>$null
    if ($LASTEXITCODE -eq 0 -and -not [string]::IsNullOrWhiteSpace([string]$stateParent)) {
        $expectedParent = ([string]$stateParent).Trim()
    }
    $stateNode = & $terraform "-chdir=$terraformDirectory" output -raw node_resource_group_name 2>$null
    if ($LASTEXITCODE -eq 0 -and -not [string]::IsNullOrWhiteSpace([string]$stateNode)) {
        $expectedNode = ([string]$stateNode).Trim()
    }
    if (-not $expectedParent.StartsWith("rg-criteriabench-", [StringComparison]::OrdinalIgnoreCase) -or
        -not $expectedNode.StartsWith("rg-criteriabench-aks-nodes-", [StringComparison]::OrdinalIgnoreCase)) {
        throw "Terraform outputs are outside the allowed CriteriaBench resource-group prefixes. Refusing teardown."
    }

    Invoke-CriteriaBenchNative -FilePath $terraform -ArgumentList @(
        "-chdir=$terraformDirectory", "apply", "-input=false", "-auto-approve",
        "-var=confirm_billable_deployment=false"
    ) -FailureMessage "Terraform teardown failed"

    $parentExists = Get-CriteriaBenchNativeOutput -FilePath $az -ArgumentList @(
        "group", "exists", "--name", $expectedParent, "--subscription", $Subscription,
        "--output", "tsv", "--only-show-errors"
    ) -FailureMessage "Azure could not verify deletion of the parent resource group"
    $nodeExists = Get-CriteriaBenchNativeOutput -FilePath $az -ArgumentList @(
        "group", "exists", "--name", $expectedNode, "--subscription", $Subscription,
        "--output", "tsv", "--only-show-errors"
    ) -FailureMessage "Azure could not verify deletion of the AKS node resource group"
    if (([string]$parentExists).Trim() -ne "false" -or ([string]$nodeExists).Trim() -ne "false") {
        throw "Terraform returned successfully, but at least one exact CriteriaBench resource group remains. Inspect Azure before any manual deletion."
    }

    $remainingState = & $terraform "-chdir=$terraformDirectory" state list
    if ($LASTEXITCODE -ne 0) {
        throw "Terraform state could not be checked after teardown."
    }
    if (@($remainingState).Count -gt 0) {
        throw "Terraform still tracks resources after teardown. Inspect the exact state before taking any manual action."
    }
}

foreach ($artifact in @($kubeconfigPath, $planPath, $summaryPath)) {
    if (Test-Path -LiteralPath $artifact -PathType Leaf) {
        Remove-Item -LiteralPath $artifact -Force
    }
}

Write-Host "Both CriteriaBench Azure resource groups were verified absent and local plan/kubeconfig artifacts were removed."
Write-Host "Terraform-managed cloud data is not recoverable. Check Cost Management again tomorrow because usage reporting can be delayed."
