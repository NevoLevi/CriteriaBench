[CmdletBinding()]
param(
    [Parameter(Mandatory)][string]$Subscription,
    [string]$Location = "germanywestcentral",
    [string]$VmSize = "Standard_D2as_v5"
)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
. (Join-Path $PSScriptRoot "tooling.ps1")
. (Join-Path $PSScriptRoot "azure-safety.ps1")

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
    "vm", "list-skus", "--location", $Location, "--size", $VmSize, "--all",
    "--query", "[?name=='$VmSize'] | [0].{name:name,restrictions:restrictions}",
    "--output", "json", "--only-show-errors"
) -FailureMessage "Azure could not query VM SKU availability"
$skuText = $skuJson -join [Environment]::NewLine
if ([string]::IsNullOrWhiteSpace($skuText) -or $skuText.Trim() -eq "null") {
    throw "Azure did not return SKU '$VmSize' in '$Location'."
}
$sku = $skuText | ConvertFrom-Json
if (@($sku.restrictions).Count -gt 0) {
    $messages = @($sku.restrictions | ForEach-Object { $_.reasonCode }) -join ", "
    throw "SKU '$VmSize' is restricted for this subscription in '$Location' ($messages). No resources were created."
}
Write-Host "AKS node SKU: $VmSize is currently available in $Location."

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

Write-Host "Preflight is read-only. It did not create resources or register providers."
Write-Warning "Azure budgets send delayed alerts; they are not spending caps. Deploy only after the reviewed plan is approved and destroy within eight hours."
