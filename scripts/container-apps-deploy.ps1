[CmdletBinding()]
param(
    [Parameter(Mandatory)][string]$Subscription,
    [string]$BudgetEmail = "",
    [ValidateRange(1, 15)][decimal]$BudgetAmount = 15,
    [ValidateRange(1, 30)][int]$ReviewDays = 14,
    [ValidatePattern('^sha256:[0-9a-f]{64}$')]
    [string]$ImageDigest = "sha256:94bb5ca7ebf26a331a202cacd455ce922db954f71697229df5439775f9a5b9ad",
    [string]$EvidencePath = "artifacts/azure-container-job-f9e8090.json",
    [Parameter(Mandatory)][switch]$ApproveBillableProduction,
    [Parameter(Mandatory)][switch]$StartExactlyOnePaidExecution,
    [Parameter(Mandatory)][switch]$AutoDestroyOnFailure
)

$ErrorActionPreference = "Stop"
$approvedDigest = "sha256:94bb5ca7ebf26a331a202cacd455ce922db954f71697229df5439775f9a5b9ad"
if (-not $ApproveBillableProduction -or -not $StartExactlyOnePaidExecution -or
    -not $AutoDestroyOnFailure) {
    throw "Deployment requires explicit production, one-paid-execution, and failure-cleanup switches."
}
if ($ImageDigest -ne $approvedDigest) {
    throw "Only the reviewed f9e8090 immutable image digest is approved for this proof."
}

$projectRoot = Split-Path -Parent $PSScriptRoot
. (Join-Path $PSScriptRoot "tooling.ps1")
. (Join-Path $PSScriptRoot "azure-safety.ps1")
. (Join-Path $PSScriptRoot "live-benchmark-support.ps1")
. (Join-Path $PSScriptRoot "container-apps-safety.ps1")
Add-Type -AssemblyName System.Net.Http

$az = Resolve-CriteriaBenchTool -ProjectRoot $projectRoot -Name az
$terraform = Resolve-CriteriaBenchTool -ProjectRoot $projectRoot -Name terraform
$docker = Resolve-CriteriaBenchTool -ProjectRoot $projectRoot -Name docker
$terraformDirectory = Join-Path $projectRoot "infra\container-apps"
$planPath = Join-Path $terraformDirectory ".criteriabench.tfplan"
$summaryPath = Join-Path $terraformDirectory ".criteriabench-plan-summary.json"
$resourceGroup = "rg-criteriabench-prod-demo"
$jobName = "criteriabench-live-job"
$containerName = "criteriabench-live"
$evidenceFullPath = [IO.Path]::GetFullPath((Join-Path $projectRoot $EvidencePath))
$artifactRoot = [IO.Path]::GetFullPath((Join-Path $projectRoot "artifacts"))
$artifactPrefix = $artifactRoot.TrimEnd('\', '/') + [IO.Path]::DirectorySeparatorChar
if (-not $evidenceFullPath.StartsWith($artifactPrefix, [StringComparison]::OrdinalIgnoreCase) -or
    [IO.Path]::GetExtension($evidenceFullPath) -ne ".json") {
    throw "Production evidence must be a JSON file under the project artifacts directory."
}
if (Test-Path -LiteralPath $evidenceFullPath) {
    throw "The production evidence path already exists; choose a new ignored artifact filename."
}

Invoke-CriteriaBenchNative -FilePath $az -ArgumentList @(
    "account", "set", "--subscription", $Subscription
) -FailureMessage "Unable to select the requested Azure subscription"
$account = Get-CriteriaBenchAzureAccountSnapshot -AzPath $az
if ($account.State -ne "Enabled") {
    throw "The selected Azure subscription is not enabled."
}
if ([string]::IsNullOrWhiteSpace($BudgetEmail)) {
    $BudgetEmail = [string]$account.UserName
}
if ($BudgetEmail -notmatch '^[^@\s]+@[^@\s]+\.[^@\s]+$') {
    throw "A valid budget notification email is required."
}

foreach ($namespace in @(
        "Microsoft.App", "Microsoft.KeyVault", "Microsoft.OperationalInsights",
        "Microsoft.ManagedIdentity", "Microsoft.Authorization", "Microsoft.Consumption"
    )) {
    $stateOutput = Get-CriteriaBenchNativeOutput -FilePath $az -ArgumentList @(
        "provider", "show", "--namespace", $namespace,
        "--query", "registrationState", "--output", "tsv", "--only-show-errors"
    ) -FailureMessage "Azure could not inspect provider registration for $namespace"
    if (([string]$stateOutput).Trim() -ne "Registered") {
        throw "Required Azure provider $namespace is not registered."
    }
}

$groupExists = Get-CriteriaBenchNativeOutput -FilePath $az -ArgumentList @(
    "group", "exists", "--name", $resourceGroup,
    "--subscription", $Subscription, "--output", "tsv", "--only-show-errors"
) -FailureMessage "Azure could not check the exact production-proof resource group"
if (([string]$groupExists).Trim() -ne "false") {
    throw "The exact production-proof resource group already exists; refusing overlap."
}

$null = Invoke-CriteriaBenchNative -FilePath $docker -ArgumentList @(
    "buildx", "imagetools", "inspect", "ghcr.io/nevolevi/criteriabench@$ImageDigest"
) -FailureMessage "The reviewed immutable GHCR image is not publicly retrievable"

$reviewAt = [DateTimeOffset]::UtcNow.AddDays($ReviewDays).ToString("yyyy-MM-ddTHH:mm:ssZ")
$budgetStartDate = [DateTimeOffset]::UtcNow.ToString("yyyy-MM-01T00:00:00Z")
$terraformEnvironment = @{ TF_VAR_budget_contact_email = $BudgetEmail }
$deploymentState = [pscustomobject]@{ TerraformStarted = $false }
$vaultName = $null
$executionName = $null

try {
    Invoke-CriteriaBenchWithAzureCliOnPath -AzPath $az -Action {
        $null = Invoke-CriteriaBenchNative -FilePath $terraform -ArgumentList @(
            "-chdir=$terraformDirectory", "init", "-input=false"
        ) -FailureMessage "Terraform initialization failed"

        $deploymentState.TerraformStarted = $true
        Invoke-CriteriaBenchScopedEnvironment -Variables $terraformEnvironment -Action {
            $null = Invoke-CriteriaBenchNative -FilePath $terraform -ArgumentList @(
                "-chdir=$terraformDirectory", "plan", "-input=false", "-out=$planPath",
                "-var=confirm_billable_deployment=true", "-var=secret_ready=false",
                "-var=image_digest=$ImageDigest", "-var=budget_amount=$BudgetAmount",
                "-var=review_at_utc=$reviewAt",
                "-var=budget_start_date=$budgetStartDate"
            ) -FailureMessage "The frozen Container Apps base plan failed"
            $basePlanJson = Get-CriteriaBenchNativeOutput -FilePath $terraform -ArgumentList @(
                "-chdir=$terraformDirectory", "show", "-json", $planPath
            ) -FailureMessage "Terraform could not render the base plan"
            Assert-CriteriaBenchContainerAppsPlan `
                -Plan (($basePlanJson -join [Environment]::NewLine) | ConvertFrom-Json) `
                -Stage base

            $null = Invoke-CriteriaBenchNative -FilePath $terraform -ArgumentList @(
                "-chdir=$terraformDirectory", "apply", "-input=false", $planPath
            ) -FailureMessage "The reviewed Container Apps base apply failed"
        }

        $vaultName = ([string](Get-CriteriaBenchNativeOutput -FilePath $terraform -ArgumentList @(
                    "-chdir=$terraformDirectory", "output", "-raw", "key_vault_name"
                ) -FailureMessage "Terraform did not return the Key Vault name")).Trim()
        $vaultUri = ([string](Get-CriteriaBenchNativeOutput -FilePath $terraform -ArgumentList @(
                    "-chdir=$terraformDirectory", "output", "-raw", "key_vault_uri"
                ) -FailureMessage "Terraform did not return the Key Vault URI")).Trim()

        $apiKey = Read-CriteriaBenchOpenAIKey -LiteralPath (Join-Path $projectRoot ".env.local")
        try {
            Set-CriteriaBenchKeyVaultSecretInMemory `
                -AzPath $az -VaultUri $vaultUri -ApiKey $apiKey `
                -ExpiresAt ([DateTimeOffset]::Parse($reviewAt))
        }
        finally {
            $apiKey = $null
        }
        Start-Sleep -Seconds 30

        Invoke-CriteriaBenchScopedEnvironment -Variables $terraformEnvironment -Action {
            $null = Invoke-CriteriaBenchNative -FilePath $terraform -ArgumentList @(
                "-chdir=$terraformDirectory", "plan", "-input=false", "-out=$planPath",
                "-var=confirm_billable_deployment=true", "-var=secret_ready=true",
                "-var=image_digest=$ImageDigest", "-var=budget_amount=$BudgetAmount",
                "-var=review_at_utc=$reviewAt",
                "-var=budget_start_date=$budgetStartDate"
            ) -FailureMessage "The frozen Container Apps job plan failed"
            $jobPlanJson = Get-CriteriaBenchNativeOutput -FilePath $terraform -ArgumentList @(
                "-chdir=$terraformDirectory", "show", "-json", $planPath
            ) -FailureMessage "Terraform could not render the job plan"
            Assert-CriteriaBenchContainerAppsPlan `
                -Plan (($jobPlanJson -join [Environment]::NewLine) | ConvertFrom-Json) `
                -Stage job
            $null = Invoke-CriteriaBenchNative -FilePath $terraform -ArgumentList @(
                "-chdir=$terraformDirectory", "apply", "-input=false", $planPath
            ) -FailureMessage "The reviewed no-ingress Container Apps Job apply failed"
        }

        $job = Get-CriteriaBenchContainerAppJobContract `
            -AzPath $az -JobName $jobName -ResourceGroup $resourceGroup
        if ($job.TriggerType -ne "Manual" -or $job.ReplicaRetryLimit -ne 0 -or
            $job.ReplicaTimeout -ne 300 -or $job.Parallelism -ne 1 -or
            $job.ReplicaCompletionCount -ne 1 -or $job.Name -ne $containerName -or
            $job.Image -ne "ghcr.io/nevolevi/criteriabench@$ImageDigest") {
            throw "The deployed job differs from the frozen execution contract."
        }

        $existingExecutions = @(
            Get-CriteriaBenchContainerAppJobExecutions `
                -AzPath $az -JobName $jobName -ResourceGroup $resourceGroup
        )
        if ($existingExecutions.Count -ne 0) {
            throw "The fresh job already has an execution; refusing a second paid start."
        }

        $executionName = Start-CriteriaBenchContainerAppJobExecution `
            -AzPath $az -JobName $jobName -ResourceGroup $resourceGroup
        if ($executionName -notmatch '^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$') {
            throw "Azure did not return a safe job execution name."
        }

        $executionStatus = ""
        for ($attempt = 1; $attempt -le 72; $attempt++) {
            $executionStatus = Get-CriteriaBenchContainerAppJobExecutionStatus `
                -AzPath $az -JobName $jobName -ResourceGroup $resourceGroup `
                -ExecutionName $executionName
            if ($executionStatus -in @("Succeeded", "Failed")) {
                break
            }
            Start-Sleep -Seconds 5
        }
        if ($executionStatus -ne "Succeeded") {
            throw "The single production job execution did not succeed (status: $executionStatus)."
        }

        $safeResult = $null
        for ($attempt = 1; $attempt -le 12; $attempt++) {
            $logs = @(& $az containerapp job logs show --name $jobName `
                    --resource-group $resourceGroup --execution $executionName `
                    --container $containerName --format text --tail 20 --only-show-errors)
            if ($LASTEXITCODE -eq 0) {
                try {
                    $safeResult = Get-CriteriaBenchSafeJobResult -LogLines $logs
                    break
                }
                catch {
                    if ($attempt -eq 12) { throw }
                }
            }
            Start-Sleep -Seconds 5
        }
        if ($null -eq $safeResult -or $safeResult.status -ne "completed") {
            throw "The production job did not return a completed sanitized benchmark result."
        }
        if ($safeResult.image_digest -ne $ImageDigest -or
            [decimal]$safeResult.authorization_guard_usd -ne [decimal]0.02 -or
            [int]$safeResult.max_attempts_per_case -ne 1) {
            throw "The sanitized job evidence differs from the reviewed cost/image contract."
        }

        $evidence = [ordered]@{
            schema_version       = 1
            status               = "completed"
            platform             = "Azure Container Apps Job"
            region               = "Germany West Central"
            resource_group       = $resourceGroup
            job_name             = $jobName
            trigger_type         = "Manual"
            ingress              = "none"
            parallelism          = 1
            replica_completions  = 1
            replica_retry_limit  = 0
            replica_timeout_s    = 300
            cpu                  = 0.25
            memory               = "0.5Gi"
            image_digest         = $ImageDigest
            review_at_utc        = $reviewAt
            budget_alert_amount  = $BudgetAmount
            budget_note          = "Delayed Azure alert; not a hard spending cap."
            execution_count      = 1
            result               = $safeResult
        }
        $evidenceDirectory = Split-Path -Parent $evidenceFullPath
        if (-not (Test-Path -LiteralPath $evidenceDirectory -PathType Container)) {
            $null = New-Item -ItemType Directory -Path $evidenceDirectory
        }
        Write-CriteriaBenchUtf8NoBom -LiteralPath $evidenceFullPath -Content (
            ($evidence | ConvertTo-Json -Depth 8) + [Environment]::NewLine
        )
    }
}
catch {
    $originalError = $_
    if ($deploymentState.TerraformStarted) {
        Write-Warning "Production deployment or proof failed; automatically cleaning the exact project stack."
        try {
            & (Join-Path $PSScriptRoot "container-apps-destroy.ps1") `
                -Subscription $Subscription `
                -Confirmation "DESTROY-CRITERIABENCH-CONTAINER-JOB" `
                -ImageDigest $ImageDigest
            if ($LASTEXITCODE -ne 0) {
                throw "Cleanup script returned exit code $LASTEXITCODE."
            }
        }
        catch {
            Write-Error "AUTOMATIC CONTAINER APPS CLEANUP WAS NOT VERIFIED. Inspect only rg-criteriabench-prod-demo, its budget, and the exact project Key Vault." -ErrorAction Continue
        }
    }
    throw $originalError
}
finally {
    $terraformEnvironment.Clear()
    $BudgetEmail = $null
}

Write-Host "CriteriaBench production job is deployed with no ingress and one successful bounded execution."
Write-Host "Sanitized evidence: $evidenceFullPath"
Write-Host "Operator review-by: $reviewAt (tag only; not automatic teardown)."
Write-Warning "The job remains deployed but idle. Never start it again without a new explicit paid authorization; the Azure budget is a delayed alert, not a hard cap."
