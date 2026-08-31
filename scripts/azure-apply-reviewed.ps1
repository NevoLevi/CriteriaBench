[CmdletBinding()]
param(
    [Parameter(Mandatory)][string]$Subscription,
    [Parameter(Mandatory)][ValidatePattern("^[a-fA-F0-9]{64}$")][string]$ReviewedPlanSha256,
    [Parameter(Mandatory)][ValidatePattern("^[a-fA-F0-9]{64}$")][string]$ReviewedSummarySha256,
    [Parameter(Mandatory)][ValidatePattern("^sha256:[a-fA-F0-9]{64}$")][string]$ImageDigest,
    [Parameter(Mandatory)][switch]$ApproveReviewedBillablePlan,
    [Parameter(Mandatory)][switch]$AutoDestroyOnFailure
)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
. (Join-Path $PSScriptRoot "tooling.ps1")
. (Join-Path $PSScriptRoot "azure-safety.ps1")

$az = Resolve-CriteriaBenchTool -ProjectRoot $projectRoot -Name az
$docker = Resolve-CriteriaBenchTool -ProjectRoot $projectRoot -Name docker
$helm = Resolve-CriteriaBenchTool -ProjectRoot $projectRoot -Name helm
$terraform = Resolve-CriteriaBenchTool -ProjectRoot $projectRoot -Name terraform
$terraformDirectory = Join-Path $projectRoot "infra\azure"
$planPath = Join-Path $terraformDirectory ".criteriabench.tfplan"
$summaryPath = Join-Path $terraformDirectory ".criteriabench-plan-summary.json"
$kubeconfigPath = Join-Path $terraformDirectory ".kubeconfig"
$null = Assert-CriteriaBenchChildPath -ProjectRoot $projectRoot -TargetPath $terraformDirectory

if (-not $ApproveReviewedBillablePlan) {
    throw "The reviewed billable Terraform plan was not explicitly approved."
}
if (-not $AutoDestroyOnFailure) {
    throw "This short-lived demo requires approval to auto-destroy ephemeral resources after any failed apply or validation."
}
if (-not (Test-Path -LiteralPath $planPath -PathType Leaf) -or -not (Test-Path -LiteralPath $summaryPath -PathType Leaf)) {
    throw "The saved plan or plan summary is missing. Run azure-plan.ps1 and review both artifacts first."
}

$actualPlanHash = (Get-FileHash -LiteralPath $planPath -Algorithm SHA256).Hash.ToLowerInvariant()
$actualSummaryHash = (Get-FileHash -LiteralPath $summaryPath -Algorithm SHA256).Hash.ToLowerInvariant()
if ($actualPlanHash -ne $ReviewedPlanSha256.ToLowerInvariant()) {
    throw "The saved Terraform plan does not match the reviewed SHA-256. Refusing to apply it."
}
if ($actualSummaryHash -ne $ReviewedSummarySha256.ToLowerInvariant()) {
    throw "The plan summary does not match the reviewed SHA-256. Refusing to apply it."
}

$summary = Get-Content -Raw -LiteralPath $summaryPath | ConvertFrom-Json
if ($summary.schema_version -ne 1 -or $summary.plan_sha256 -ne $actualPlanHash) {
    throw "The reviewed summary is not bound to this Terraform plan."
}
$expiresAt = [DateTimeOffset]::Parse($summary.expires_at_utc)
if ($expiresAt -le [DateTimeOffset]::UtcNow.AddMinutes(30)) {
    throw "The reviewed plan expires too soon or has expired. Create and review a fresh plan."
}
if ($Subscription -ne $summary.subscription_name -and $Subscription -ne $summary.subscription_id) {
    throw "The reviewed plan was prepared for a different Azure subscription."
}

# Install exact, checksum-verified project-local clients before any Azure
# resource/provider mutation. Global clients are not silently trusted here.
$projectKubectl = Join-Path $projectRoot ".tools\kubectl\kubectl.exe"
$projectKubelogin = Join-Path $projectRoot ".tools\kubelogin\kubelogin.exe"
if (-not (Test-Path -LiteralPath $projectKubectl -PathType Leaf) -or -not (Test-Path -LiteralPath $projectKubelogin -PathType Leaf)) {
    & (Join-Path $PSScriptRoot "install-kubernetes-tools.ps1")
}
$kubectl = Resolve-CriteriaBenchTool -ProjectRoot $projectRoot -Name kubectl
$kubelogin = Resolve-CriteriaBenchTool -ProjectRoot $projectRoot -Name kubelogin

# Confirm the exact immutable public image exists before cloud resources can be
# created. Routine services still run in mock-only mode and receive no API key.
Invoke-CriteriaBenchNative -FilePath $docker -ArgumentList @(
    "buildx", "imagetools", "inspect", "ghcr.io/nevolevi/criteriabench@$ImageDigest"
) -FailureMessage "The approved GHCR image digest is not publicly retrievable"

Invoke-CriteriaBenchNative -FilePath $az -ArgumentList @(
    "account", "set", "--subscription", $summary.subscription_id
) -FailureMessage "Unable to select the reviewed Azure subscription"
$accountJson = Get-CriteriaBenchNativeOutput -FilePath $az -ArgumentList @(
    "account", "show", "--query", "{id:id,name:name,state:state}",
    "--output", "json", "--only-show-errors"
) -FailureMessage "Unable to verify the current Azure subscription"
$account = (($accountJson -join [Environment]::NewLine) | ConvertFrom-Json)
if ($account.id -ne $summary.subscription_id -or $account.name -ne $summary.subscription_name -or $account.state -ne "Enabled") {
    throw "The current Azure account does not match the hash-bound plan summary."
}

foreach ($namespace in @("Microsoft.ContainerService", "Microsoft.Compute", "Microsoft.Network", "Microsoft.Storage", "Microsoft.Consumption")) {
    $state = Get-CriteriaBenchNativeOutput -FilePath $az -ArgumentList @(
        "provider", "show", "--namespace", $namespace,
        "--query", "registrationState", "--output", "tsv", "--only-show-errors"
    ) -FailureMessage "Failed to query Azure provider $namespace"
    if (([string]$state).Trim() -ne "Registered") {
        Invoke-CriteriaBenchNative -FilePath $az -ArgumentList @(
            "provider", "register", "--namespace", $namespace, "--wait", "--only-show-errors"
        ) -FailureMessage "Failed to register required Azure provider $namespace before Terraform apply"
    }
}

$previousKubeconfig = [Environment]::GetEnvironmentVariable("KUBECONFIG", "Process")
$env:KUBECONFIG = $kubeconfigPath
$terraformStarted = $false

try {
    # Set before invocation: a nonzero Terraform exit may still leave real
    # resources in state, so the catch path must always attempt teardown.
    $terraformStarted = $true
    Invoke-CriteriaBenchNative -FilePath $terraform -ArgumentList @(
        "-chdir=$terraformDirectory", "apply", "-input=false", $planPath
    ) -FailureMessage "Terraform apply failed"

    $resourceGroup = [string](Get-CriteriaBenchNativeOutput -FilePath $terraform -ArgumentList @(
        "-chdir=$terraformDirectory", "output", "-raw", "resource_group_name"
    ) -FailureMessage "Terraform did not return the resource group output")
    $nodeResourceGroup = [string](Get-CriteriaBenchNativeOutput -FilePath $terraform -ArgumentList @(
        "-chdir=$terraformDirectory", "output", "-raw", "node_resource_group_name"
    ) -FailureMessage "Terraform did not return the node resource group output")
    $clusterName = [string](Get-CriteriaBenchNativeOutput -FilePath $terraform -ArgumentList @(
        "-chdir=$terraformDirectory", "output", "-raw", "cluster_name"
    ) -FailureMessage "Terraform did not return the cluster output")
    $resourceGroup = $resourceGroup.Trim()
    $nodeResourceGroup = $nodeResourceGroup.Trim()
    $clusterName = $clusterName.Trim()
    if ($resourceGroup -ne $summary.expected_resource_group -or
        $nodeResourceGroup -ne $summary.expected_node_resource_group -or
        $clusterName -ne $summary.expected_cluster_name) {
        throw "Terraform outputs differ from the reviewed project-scoped targets."
    }

    Invoke-CriteriaBenchNative -FilePath $az -ArgumentList @(
        "aks", "get-credentials", "--resource-group", $resourceGroup,
        "--name", $clusterName, "--file", $kubeconfigPath,
        "--overwrite-existing", "--only-show-errors"
    ) -FailureMessage "AKS credentials could not be configured"
    Invoke-CriteriaBenchNative -FilePath $kubelogin -ArgumentList @(
        "convert-kubeconfig", "-l", "azurecli", "--kubeconfig", $kubeconfigPath
    ) -FailureMessage "Microsoft Entra kubeconfig conversion failed"

    $authorized = $false
    for ($attempt = 1; $attempt -le 12; $attempt++) {
        $canI = & $kubectl auth can-i get pods --all-namespaces --request-timeout=10s
        $canIExitCode = $LASTEXITCODE
        if ($canIExitCode -eq 0 -and (($canI -join "`n").Trim() -eq "yes")) {
            $authorized = $true
            break
        }
        Start-Sleep -Seconds 10
    }
    if (-not $authorized) {
        throw "Azure RBAC did not become effective within two minutes."
    }

    $namespaceYaml = & $kubectl create namespace criteriabench --dry-run=client --output yaml
    if ($LASTEXITCODE -ne 0) {
        throw "kubectl could not render the CriteriaBench namespace."
    }
    $namespaceYaml | & $kubectl apply -f -
    if ($LASTEXITCODE -ne 0) {
        throw "kubectl could not apply the CriteriaBench namespace."
    }
    Invoke-CriteriaBenchNative -FilePath $kubectl -ArgumentList @(
        "label", "namespace", "criteriabench",
        "pod-security.kubernetes.io/enforce=restricted",
        "pod-security.kubernetes.io/audit=restricted",
        "pod-security.kubernetes.io/warn=restricted", "--overwrite"
    ) -FailureMessage "Could not apply restricted Pod Security Admission labels"

    Invoke-CriteriaBenchNative -FilePath $helm -ArgumentList @(
        "upgrade", "--install", "criteriabench", (Join-Path $projectRoot "deploy\helm\criteriabench"),
        "--namespace", "criteriabench",
        "--values", (Join-Path $projectRoot "deploy\helm\criteriabench\values-azure-demo.yaml"),
        "--set-string", "image.repository=ghcr.io/nevolevi/criteriabench",
        "--set-string", "image.digest=$ImageDigest",
        "--rollback-on-failure", "--wait", "--wait-for-jobs", "--timeout", "10m"
    ) -FailureMessage "Helm deployment failed"
    Invoke-CriteriaBenchNative -FilePath $kubectl -ArgumentList @(
        "--namespace", "criteriabench", "rollout", "status",
        "deployment/criteriabench-api", "--timeout=5m"
    ) -FailureMessage "The API rollout did not become healthy"
    Invoke-CriteriaBenchNative -FilePath $kubectl -ArgumentList @(
        "--namespace", "criteriabench", "rollout", "status",
        "deployment/criteriabench-worker", "--timeout=5m"
    ) -FailureMessage "The worker rollout did not become healthy"
}
catch {
    $originalError = $_
    if ($terraformStarted) {
        Write-Warning "Apply/deployment validation failed. Automatically destroying the approved ephemeral stack now."
        & $terraform "-chdir=$terraformDirectory" apply -input=false -auto-approve "-var=confirm_billable_deployment=false"
        $cleanupExitCode = $LASTEXITCODE

        $parentExists = & $az group exists --name $summary.expected_resource_group --subscription $summary.subscription_id --output tsv --only-show-errors
        $parentCheckExit = $LASTEXITCODE
        $nodeExists = & $az group exists --name $summary.expected_node_resource_group --subscription $summary.subscription_id --output tsv --only-show-errors
        $nodeCheckExit = $LASTEXITCODE
        $cleanupVerified = $cleanupExitCode -eq 0 -and $parentCheckExit -eq 0 -and $nodeCheckExit -eq 0 -and
            ([string]$parentExists).Trim() -eq "false" -and ([string]$nodeExists).Trim() -eq "false"

        if (-not $cleanupVerified) {
            Write-Error "AUTOMATIC TEARDOWN WAS NOT VERIFIED. Immediately run scripts\azure-destroy.ps1 with the exact reviewed subscription and inspect both CriteriaBench resource groups in Azure." -ErrorAction Continue
        }
        elseif (Test-Path -LiteralPath $kubeconfigPath -PathType Leaf) {
            Remove-Item -LiteralPath $kubeconfigPath -Force
        }
    }
    throw $originalError
}
finally {
    [Environment]::SetEnvironmentVariable("KUBECONFIG", $previousKubeconfig, "Process")
}

Write-Host "CriteriaBench is deployed from immutable image digest $ImageDigest in mock-only mode."
Write-Host "Teardown deadline (UTC): $($summary.expires_at_utc)"
Write-Host "Project kubeconfig: $kubeconfigPath"
Write-Host "Port-forward: kubectl -n criteriabench port-forward service/criteriabench-api 8000:80"
Write-Warning "Destroy immediately after evidence capture. The Azure budget is delayed notification and cannot stop spend."
