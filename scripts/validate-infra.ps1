[CmdletBinding()]
param([switch]$BuildContainer)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
. (Join-Path $PSScriptRoot "tooling.ps1")
. (Join-Path $PSScriptRoot "azure-safety.ps1")

$docker = Resolve-CriteriaBenchTool -ProjectRoot $projectRoot -Name docker
$helm = Resolve-CriteriaBenchTool -ProjectRoot $projectRoot -Name helm
$kubectl = Resolve-CriteriaBenchTool -ProjectRoot $projectRoot -Name kubectl
$terraform = Resolve-CriteriaBenchTool -ProjectRoot $projectRoot -Name terraform
$terraformDirectory = Join-Path $projectRoot "infra\azure"

Push-Location $projectRoot
try {
    & (Join-Path $PSScriptRoot "compose-safe.ps1") config --quiet
    if ($LASTEXITCODE -ne 0) {
        throw "Docker Compose validation failed"
    }
    Invoke-CriteriaBenchNative -FilePath $helm -ArgumentList @(
        "lint", "deploy/helm/criteriabench", "--strict"
    ) -FailureMessage "Helm lint failed"
    Invoke-CriteriaBenchNative -FilePath $helm -ArgumentList @(
        "template", "criteriabench", "deploy/helm/criteriabench",
        "--namespace", "criteriabench"
    ) -FailureMessage "Helm rendering failed" | Out-Null
    Invoke-CriteriaBenchNative -FilePath $kubectl -ArgumentList @(
        "kustomize", "deploy/k8s/overlays/kind"
    ) -FailureMessage "Kustomize rendering failed" | Out-Null
    Invoke-CriteriaBenchNative -FilePath $terraform -ArgumentList @(
        "-chdir=$terraformDirectory", "fmt", "-check", "-recursive"
    ) -FailureMessage "Terraform formatting check failed"
    Invoke-CriteriaBenchNative -FilePath $terraform -ArgumentList @(
        "-chdir=$terraformDirectory", "init", "-backend=false", "-input=false"
    ) -FailureMessage "Terraform initialization failed"
    Invoke-CriteriaBenchNative -FilePath $terraform -ArgumentList @(
        "-chdir=$terraformDirectory", "validate"
    ) -FailureMessage "Terraform validation failed"
    if ($BuildContainer) {
        Invoke-CriteriaBenchNative -FilePath $docker -ArgumentList @(
            "build", "--tag", "criteriabench:validation", "."
        ) -FailureMessage "Container build failed"
    }
}
finally {
    Pop-Location
}

Write-Host "Infrastructure definitions are valid. No Azure resources were created."
