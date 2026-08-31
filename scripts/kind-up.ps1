[CmdletBinding()]
param(
    [ValidateSet("criteriabench")][string]$ClusterName = "criteriabench"
)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
. (Join-Path $PSScriptRoot "tooling.ps1")
. (Join-Path $PSScriptRoot "azure-safety.ps1")

function Wait-CriteriaBenchLoopbackHealth {
    [CmdletBinding()]
    param(
        [ValidateRange(1, 60)][int]$Attempts = 30,
        [ValidateRange(100, 5000)][int]$DelayMilliseconds = 500
    )

    for ($attempt = 1; $attempt -le $Attempts; $attempt++) {
        try {
            $response = Invoke-WebRequest -UseBasicParsing `
                -Uri "http://127.0.0.1:8080/healthz" -TimeoutSec 3
            if ($response.StatusCode -eq 200) {
                return
            }
        }
        catch {
            # A freshly restarted NodePort can briefly close a connection even
            # after the Deployment reports Available. Retry without echoing the
            # response or exception, which may contain unsafe diagnostic data.
        }

        if ($attempt -lt $Attempts) {
            Start-Sleep -Milliseconds $DelayMilliseconds
        }
    }

    throw "CriteriaBench API did not pass its loopback health check after $Attempts attempts."
}

$docker = Resolve-CriteriaBenchTool -ProjectRoot $projectRoot -Name docker
$kind = Resolve-CriteriaBenchTool -ProjectRoot $projectRoot -Name kind
$kubectl = Resolve-CriteriaBenchTool -ProjectRoot $projectRoot -Name kubectl -Optional
if (-not $kubectl) {
    & (Join-Path $PSScriptRoot "install-kubernetes-tools.ps1")
    $kubectl = Resolve-CriteriaBenchTool -ProjectRoot $projectRoot -Name kubectl
}

$kubeconfigDirectory = Join-Path $projectRoot ".tools\kind"
$kubeconfigPath = Join-Path $kubeconfigDirectory "criteriabench.kubeconfig"
New-Item -ItemType Directory -Force -Path $kubeconfigDirectory | Out-Null
$previousKubeconfig = [Environment]::GetEnvironmentVariable("KUBECONFIG", "Process")
$env:KUBECONFIG = $kubeconfigPath

Push-Location $projectRoot
try {
    Invoke-CriteriaBenchNative -FilePath $docker -ArgumentList @("info") -FailureMessage "Docker Desktop is not ready"
    Invoke-CriteriaBenchNative -FilePath $docker -ArgumentList @(
        "build", "--tag", "criteriabench:local", "."
    ) -FailureMessage "CriteriaBench container build failed"

    $existingClusters = @(& $kind get clusters)
    if ($LASTEXITCODE -ne 0) {
        throw "kind could not list local clusters."
    }
    if ($existingClusters -notcontains $ClusterName) {
        Invoke-CriteriaBenchNative -FilePath $kind -ArgumentList @(
            "create", "cluster", "--name", $ClusterName,
            "--config", "deploy/kind/cluster.yaml", "--wait", "180s"
        ) -FailureMessage "kind cluster creation failed"
    }

    Invoke-CriteriaBenchNative -FilePath $kind -ArgumentList @(
        "load", "docker-image", "criteriabench:local", "--name", $ClusterName
    ) -FailureMessage "kind could not load the local CriteriaBench image"
    Invoke-CriteriaBenchNative -FilePath $kubectl -ArgumentList @(
        "config", "use-context", "kind-$ClusterName"
    ) -FailureMessage "kubectl could not select the project-local kind context"
    # A reused kind cluster must rerun the immutable migration Job and replace
    # pods after reloading the constant local image tag. Otherwise a second run
    # can report healthy while still exercising stale code and schema.
    Invoke-CriteriaBenchNative -FilePath $kubectl -ArgumentList @(
        "apply", "--filename", "deploy/k8s/base/namespace.yaml"
    ) -FailureMessage "CriteriaBench namespace could not be prepared"
    Invoke-CriteriaBenchNative -FilePath $kubectl -ArgumentList @(
        "--namespace", "criteriabench", "delete", "job/migrate",
        "--ignore-not-found=true", "--wait=true"
    ) -FailureMessage "The prior local migration Job could not be removed"
    Invoke-CriteriaBenchNative -FilePath $kubectl -ArgumentList @(
        "apply", "--kustomize", "deploy/k8s/overlays/kind"
    ) -FailureMessage "Kustomize deployment failed"
    Invoke-CriteriaBenchNative -FilePath $kubectl -ArgumentList @(
        "--namespace", "criteriabench", "rollout", "restart", "deployment/api"
    ) -FailureMessage "The API could not be restarted onto the freshly loaded image"
    Invoke-CriteriaBenchNative -FilePath $kubectl -ArgumentList @(
        "--namespace", "criteriabench", "rollout", "restart", "deployment/worker"
    ) -FailureMessage "The worker could not be restarted onto the freshly loaded image"
    Invoke-CriteriaBenchNative -FilePath $kubectl -ArgumentList @(
        "--namespace", "criteriabench", "rollout", "status",
        "statefulset/postgres", "--timeout=180s"
    ) -FailureMessage "PostgreSQL did not become ready"
    Invoke-CriteriaBenchNative -FilePath $kubectl -ArgumentList @(
        "--namespace", "criteriabench", "rollout", "status",
        "deployment/redis", "--timeout=180s"
    ) -FailureMessage "Redis did not become ready"
    Invoke-CriteriaBenchNative -FilePath $kubectl -ArgumentList @(
        "--namespace", "criteriabench", "wait", "--for=condition=complete",
        "job/migrate", "--timeout=180s"
    ) -FailureMessage "Database migration did not complete"
    Invoke-CriteriaBenchNative -FilePath $kubectl -ArgumentList @(
        "--namespace", "criteriabench", "rollout", "status",
        "deployment/api", "--timeout=180s"
    ) -FailureMessage "API did not become ready"
    Invoke-CriteriaBenchNative -FilePath $kubectl -ArgumentList @(
        "--namespace", "criteriabench", "rollout", "status",
        "deployment/worker", "--timeout=180s"
    ) -FailureMessage "Worker did not become ready"

    Wait-CriteriaBenchLoopbackHealth
}
finally {
    Pop-Location
    [Environment]::SetEnvironmentVariable("KUBECONFIG", $previousKubeconfig, "Process")
}

Write-Host "CriteriaBench is ready at http://127.0.0.1:8080/docs in mock-only mode."
