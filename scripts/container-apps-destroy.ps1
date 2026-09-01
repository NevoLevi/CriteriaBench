[CmdletBinding()]
param(
    [Parameter(Mandatory)][string]$Subscription,
    [Parameter(Mandatory)]
    [ValidateSet("DESTROY-CRITERIABENCH-CONTAINER-JOB")]
    [string]$Confirmation,
    [string]$ImageDigest = "sha256:94bb5ca7ebf26a331a202cacd455ce922db954f71697229df5439775f9a5b9ad"
)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
. (Join-Path $PSScriptRoot "tooling.ps1")
. (Join-Path $PSScriptRoot "azure-safety.ps1")
. (Join-Path $PSScriptRoot "live-benchmark-support.ps1")
. (Join-Path $PSScriptRoot "container-apps-safety.ps1")

$az = Resolve-CriteriaBenchTool -ProjectRoot $projectRoot -Name az
$terraform = Resolve-CriteriaBenchTool -ProjectRoot $projectRoot -Name terraform
$terraformDirectory = Join-Path $projectRoot "infra\container-apps"
$planPath = Join-Path $terraformDirectory ".criteriabench.tfplan"
$summaryPath = Join-Path $terraformDirectory ".criteriabench-plan-summary.json"
$resourceGroup = "rg-criteriabench-prod-demo"
$vaultName = $null

Invoke-CriteriaBenchNative -FilePath $az -ArgumentList @(
    "account", "set", "--subscription", $Subscription
) -FailureMessage "Unable to select the requested Azure subscription"
$account = Get-CriteriaBenchAzureAccountSnapshot -AzPath $az
$budgetEmail = [string]$account.UserName
if ($budgetEmail -notmatch '^[^@\s]+@[^@\s]+\.[^@\s]+$') {
    throw "The signed-in Azure identity is not an email-shaped budget contact."
}
$reviewAt = [DateTimeOffset]::UtcNow.AddHours(1).ToString("yyyy-MM-ddTHH:mm:ssZ")
$budgetStartDate = [DateTimeOffset]::UtcNow.ToString("yyyy-MM-01T00:00:00Z")
$terraformEnvironment = @{
    TF_VAR_budget_contact_email = $budgetEmail
}

Invoke-CriteriaBenchWithAzureCliOnPath -AzPath $az -Action {
    Invoke-CriteriaBenchNative -FilePath $terraform -ArgumentList @(
        "-chdir=$terraformDirectory", "init", "-input=false"
    ) -FailureMessage "Terraform initialization failed"

    $stateVault = & $terraform "-chdir=$terraformDirectory" output -raw key_vault_name 2>$null
    if ($LASTEXITCODE -eq 0 -and -not [string]::IsNullOrWhiteSpace([string]$stateVault)) {
        $vaultName = ([string]$stateVault).Trim()
    }

    Invoke-CriteriaBenchScopedEnvironment -Variables $terraformEnvironment -Action {
        Invoke-CriteriaBenchNative -FilePath $terraform -ArgumentList @(
            "-chdir=$terraformDirectory", "apply", "-input=false", "-auto-approve",
            "-var=confirm_billable_deployment=false",
            "-var=secret_ready=false",
            "-var=image_digest=$ImageDigest",
            "-var=review_at_utc=$reviewAt",
            "-var=budget_start_date=$budgetStartDate"
        ) -FailureMessage "Container Apps production-proof teardown failed"
    }

    $groupExists = Get-CriteriaBenchNativeOutput -FilePath $az -ArgumentList @(
        "group", "exists", "--name", $resourceGroup,
        "--subscription", $Subscription, "--output", "tsv", "--only-show-errors"
    ) -FailureMessage "Azure could not verify deletion of the production-proof resource group"
    if (([string]$groupExists).Trim() -ne "false") {
        throw "The exact production-proof resource group still exists after teardown."
    }

    if (-not [string]::IsNullOrWhiteSpace($vaultName)) {
        $deletedCount = Get-CriteriaBenchDeletedKeyVaultExactMatchCount `
            -AzPath $az -VaultName $vaultName
        if ($deletedCount -notin @(0, 1)) {
            throw "Azure returned an unexpected soft-deleted Key Vault count."
        }
        if ($deletedCount -eq 1) {
            Write-Warning "The exact project Key Vault remains soft-deleted as an expected non-billable residual. Ordinary cleanup never purges it."
        }
        else {
            Write-Host "No soft-deleted project Key Vault residual was found."
        }
    }

    $remainingState = @(& $terraform "-chdir=$terraformDirectory" state list)
    if ($LASTEXITCODE -ne 0) {
        throw "Terraform state could not be inspected after teardown."
    }
    $managed = @(Get-CriteriaBenchTerraformManagedStateEntry -Address $remainingState)
    if ($managed.Count -gt 0) {
        throw "Terraform still tracks managed Container Apps resources after teardown."
    }
}

foreach ($artifact in @($planPath, $summaryPath)) {
    if (Test-Path -LiteralPath $artifact -PathType Leaf) {
        Remove-Item -LiteralPath $artifact -Force
    }
}
$terraformEnvironment.Clear()
$budgetEmail = $null

Write-Host "Active Container Apps production-proof resources and budget were removed; Terraform retains no managed resources. Any exact soft-deleted Key Vault residual was reported and not purged."
