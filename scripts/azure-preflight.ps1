[CmdletBinding()]
param(
    [Parameter(Mandatory)][string]$Subscription,
    [string]$Location = "germanywestcentral",
    [ValidateSet("Standard_D2as_v4")][string]$VmSize = "Standard_D2as_v4"
)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
. (Join-Path $PSScriptRoot "tooling.ps1")
. (Join-Path $PSScriptRoot "azure-safety.ps1")
. (Join-Path $PSScriptRoot "azure-json.ps1")

$az = Resolve-CriteriaBenchTool -ProjectRoot $projectRoot -Name az
$null = Resolve-CriteriaBenchTool -ProjectRoot $projectRoot -Name terraform

$accountJson = Get-CriteriaBenchNativeOutput -FilePath $az -ArgumentList @(
    "account", "show", "--subscription", $Subscription,
    "--query", "{id:id,name:name,state:state,tenantId:tenantId}",
    "--output", "json", "--only-show-errors"
) -FailureMessage "Azure CLI is not signed in to the requested subscription"
$account = (($accountJson -join [Environment]::NewLine) | ConvertFrom-Json)
if ($account.state -ne "Enabled") {
    throw "Azure subscription '$($account.name)' is not enabled."
}
Write-Host "Azure subscription: $($account.name) (enabled)"

$skuJson = Get-CriteriaBenchNativeOutput -FilePath $az -ArgumentList @(
    "vm", "list-skus", "--location", $Location,
    "--resource-type", "virtualMachines", "--size", $VmSize, "--all",
    "--query", "[].{name:name,family:family,vcpus:capabilities[?name=='vCPUs'].value,restrictions:restrictions}",
    "--output", "json", "--only-show-errors"
) -FailureMessage "Azure could not query VM SKU availability"
$skuText = $skuJson -join [Environment]::NewLine
$skuCandidates = @(
    ConvertFrom-CriteriaBenchJsonObjectArray `
        -Json $skuText `
        -Description "VM SKU"
)

$usageJson = Get-CriteriaBenchNativeOutput -FilePath $az -ArgumentList @(
    "vm", "list-usage", "--location", $Location,
    "--query", "[].{name:name.value,currentValue:currentValue,limit:limit}",
    "--output", "json", "--only-show-errors"
) -FailureMessage "Azure could not query regional VM quota"
$usageText = $usageJson -join [Environment]::NewLine
$usageRecords = @(
    ConvertFrom-CriteriaBenchJsonObjectArray `
        -Json $usageText `
        -Description "regional VM quota"
)

$quota = Assert-CriteriaBenchAzureVmQuota `
    -VmSize $VmSize `
    -SkuCandidates $skuCandidates `
    -UsageRecords $usageRecords `
    -NodeCount 1
Write-Host "AKS node SKU: $VmSize is unrestricted in $Location."
$quotaMessage = (
    "Quota snapshot: family {0} uses {1}/{2} vCPUs ({3} remaining); " +
    "region uses {4}/{5} ({6} remaining); this one-node plan requires {7}."
) -f
    $quota.Family,
    $quota.FamilyCurrent,
    $quota.FamilyLimit,
    $quota.FamilyRemaining,
    $quota.RegionalCurrent,
    $quota.RegionalLimit,
    $quota.RegionalRemaining,
    $quota.RequiredVcpus
Write-Host $quotaMessage

$providerNamespaces = @(
    "Microsoft.ContainerService",
    "Microsoft.Compute",
    "Microsoft.Network",
    "Microsoft.Storage",
    "Microsoft.Consumption"
)
foreach ($namespace in $providerNamespaces) {
    $state = Get-CriteriaBenchNativeOutput -FilePath $az -ArgumentList @(
        "provider", "show", "--namespace", $namespace,
        "--query", "registrationState", "--output", "tsv", "--only-show-errors"
    ) -FailureMessage "Azure could not query provider $namespace"
    Write-Host "Provider $namespace`: $(([string]$state).Trim())"
}

Write-Host "Preflight is read-only. It did not create resources, register providers, or run Terraform."
Write-Warning "Quota is a point-in-time check, not a reservation. Azure budgets are delayed alerts, not spending caps. Deploy only after reviewing a fresh plan and destroy within eight hours."
