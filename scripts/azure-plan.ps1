[CmdletBinding()]
param(
    [Parameter(Mandatory)][string]$Subscription,
    [Parameter(Mandatory)][string]$BudgetEmail,
    [ValidateRange(1, 15)][decimal]$BudgetAmount = 15,
    [ValidateRange(1, 8)][int]$TtlHours = 8,
    [Parameter(Mandatory)][switch]$ApproveBillablePlan
)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
. (Join-Path $PSScriptRoot "tooling.ps1")
. (Join-Path $PSScriptRoot "azure-safety.ps1")

$az = Resolve-CriteriaBenchTool -ProjectRoot $projectRoot -Name az
$terraform = Resolve-CriteriaBenchTool -ProjectRoot $projectRoot -Name terraform
$terraformDirectory = Join-Path $projectRoot "infra\azure"
$planPath = Join-Path $terraformDirectory ".criteriabench.tfplan"
$summaryPath = Join-Path $terraformDirectory ".criteriabench-plan-summary.json"
$null = Assert-CriteriaBenchChildPath -ProjectRoot $projectRoot -TargetPath $terraformDirectory

if (-not $ApproveBillablePlan) {
    throw "Planning was not approved. This plan can create billable resources if it is later reviewed and applied."
}

# A failed plan must never leave an older artifact that could be mistaken for
# the newly reviewed one.
if (Test-Path -LiteralPath $planPath -PathType Leaf) {
    Remove-Item -LiteralPath $planPath -Force
}
if (Test-Path -LiteralPath $summaryPath -PathType Leaf) {
    Remove-Item -LiteralPath $summaryPath -Force
}

& (Join-Path $PSScriptRoot "azure-preflight.ps1") -Subscription $Subscription

Invoke-CriteriaBenchNative -FilePath $az -ArgumentList @(
    "account", "set", "--subscription", $Subscription
) -FailureMessage "Unable to select the requested Azure subscription"

$accountJson = Get-CriteriaBenchNativeOutput -FilePath $az -ArgumentList @(
    "account", "show", "--query", "{id:id,name:name,state:state}",
    "--output", "json", "--only-show-errors"
) -FailureMessage "Unable to verify the selected Azure subscription"
$account = (($accountJson -join [Environment]::NewLine) | ConvertFrom-Json)
if ($account.state -ne "Enabled") {
    throw "The selected Azure subscription is not enabled."
}

$createdAt = [DateTimeOffset]::UtcNow
$expiresAt = $createdAt.AddHours($TtlHours)
$expiresAtText = $expiresAt.ToString("yyyy-MM-ddTHH:mm:ssZ")
$previousContactEmails = [Environment]::GetEnvironmentVariable("TF_VAR_budget_contact_emails", "Process")
$env:TF_VAR_budget_contact_emails = ConvertTo-Json -Compress -InputObject @($BudgetEmail)

try {
    Invoke-CriteriaBenchWithAzureCliOnPath -AzPath $az -Action {
        Invoke-CriteriaBenchNative -FilePath $terraform -ArgumentList @(
            "-chdir=$terraformDirectory", "init", "-input=false"
        ) -FailureMessage "Terraform initialization failed"
        Invoke-CriteriaBenchNative -FilePath $terraform -ArgumentList @(
            "-chdir=$terraformDirectory", "fmt", "-check", "-recursive"
        ) -FailureMessage "Terraform formatting check failed"
        Invoke-CriteriaBenchNative -FilePath $terraform -ArgumentList @(
            "-chdir=$terraformDirectory", "validate"
        ) -FailureMessage "Terraform validation failed"
        Invoke-CriteriaBenchNative -FilePath $terraform -ArgumentList @(
            "-chdir=$terraformDirectory", "plan", "-input=false", "-out=$planPath",
            "-var=confirm_billable_deployment=true",
            "-var=deployment_ttl_hours=$TtlHours",
            "-var=expires_at_utc=$expiresAtText",
            "-var=budget_amount=$BudgetAmount"
        ) -FailureMessage "Terraform could not create a reviewed plan; nothing was applied"
    }
}
finally {
    [Environment]::SetEnvironmentVariable("TF_VAR_budget_contact_emails", $previousContactEmails, "Process")
}

$planHash = (Get-FileHash -LiteralPath $planPath -Algorithm SHA256).Hash.ToLowerInvariant()
$summary = [ordered]@{
    schema_version                = 1
    plan_sha256                  = $planHash
    subscription_id              = $account.id
    subscription_name            = $account.name
    created_at_utc               = $createdAt.ToString("yyyy-MM-ddTHH:mm:ssZ")
    expires_at_utc               = $expiresAtText
    ttl_hours                    = $TtlHours
    budget_amount                = $BudgetAmount
    budget_currency              = "subscription billing currency"
    expected_resource_group      = "rg-criteriabench-demo"
    expected_node_resource_group = "rg-criteriabench-aks-nodes-demo"
    expected_cluster_name        = "aks-criteriabench-demo"
    expected_resources           = "AKS Free-tier control plane; one D2-class node; parent and managed node resource groups; no managed database or observability service"
}
$summaryJson = $summary | ConvertTo-Json
Write-CriteriaBenchUtf8NoBom -LiteralPath $summaryPath -Content ($summaryJson + [Environment]::NewLine)
$summaryHash = (Get-FileHash -LiteralPath $summaryPath -Algorithm SHA256).Hash.ToLowerInvariant()

Write-Host "Terraform plan created but NOT applied."
Write-Host "Plan SHA-256:    $planHash"
Write-Host "Summary SHA-256: $summaryHash"
Write-Host "Review with: $terraform -chdir=$terraformDirectory show $planPath"
Write-Warning "The budget is an alert, not a spending cap. Apply only after approving this exact plan+summary hash pair and confirming current regional pricing."
