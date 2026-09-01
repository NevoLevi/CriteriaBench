Set-StrictMode -Version Latest

function ConvertFrom-CriteriaBenchContainerAppsJsonObject {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)][AllowEmptyString()][string]$Json,
        [Parameter(Mandatory)][string]$Description
    )

    $trimmedJson = $Json.Trim()
    if (-not $trimmedJson.StartsWith("{", [StringComparison]::Ordinal) -or
        -not $trimmedJson.EndsWith("}", [StringComparison]::Ordinal)) {
        throw "Azure $Description response was not a JSON object."
    }
    try {
        $parsed = $trimmedJson | ConvertFrom-Json -ErrorAction Stop
    }
    catch {
        throw "Azure $Description response was not valid JSON."
    }
    if ($parsed -isnot [System.Management.Automation.PSCustomObject]) {
        throw "Azure $Description response was not a JSON object."
    }
    return $parsed
}

function ConvertFrom-CriteriaBenchContainerAppsJsonObjectArray {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)][AllowEmptyString()][string]$Json,
        [Parameter(Mandatory)][string]$Description
    )

    $trimmedJson = $Json.Trim()
    if (-not $trimmedJson.StartsWith("[", [StringComparison]::Ordinal) -or
        -not $trimmedJson.EndsWith("]", [StringComparison]::Ordinal)) {
        throw "Azure $Description response was not a JSON object array."
    }
    try {
        $parsed = $trimmedJson | ConvertFrom-Json -ErrorAction Stop
    }
    catch {
        throw "Azure $Description response was not valid JSON."
    }

    # Windows PowerShell 5 unwraps a one-element JSON array. Explicit pipeline
    # enumeration gives callers the same object stream for zero, one, or many items.
    foreach ($item in $parsed) {
        if ($null -eq $item -or
            $item -isnot [System.Management.Automation.PSCustomObject]) {
            throw "Azure $Description response contained a non-object array item."
        }
        $item
    }
}

function Get-CriteriaBenchContainerAppsRequiredObjectProperty {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)][object]$Object,
        [Parameter(Mandatory)][string]$Name,
        [Parameter(Mandatory)][string]$Description
    )

    if ($Object -isnot [System.Management.Automation.PSCustomObject]) {
        throw "Azure $Description parent was not a JSON object."
    }
    $property = $Object.PSObject.Properties[$Name]
    if ($null -eq $property -or
        $property.Value -isnot [System.Management.Automation.PSCustomObject]) {
        throw "Azure $Description response omitted object property '$Name'."
    }
    return $property.Value
}

function Get-CriteriaBenchContainerAppsRequiredStringProperty {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)][object]$Object,
        [Parameter(Mandatory)][string]$Name,
        [Parameter(Mandatory)][string]$Description
    )

    if ($Object -isnot [System.Management.Automation.PSCustomObject]) {
        throw "Azure $Description parent was not a JSON object."
    }
    $property = $Object.PSObject.Properties[$Name]
    if ($null -eq $property -or $property.Value -isnot [string] -or
        [string]::IsNullOrWhiteSpace([string]$property.Value)) {
        throw "Azure $Description response omitted string property '$Name'."
    }
    return ([string]$property.Value).Trim()
}

function Get-CriteriaBenchContainerAppsRequiredIntProperty {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)][object]$Object,
        [Parameter(Mandatory)][string]$Name,
        [Parameter(Mandatory)][string]$Description
    )

    if ($Object -isnot [System.Management.Automation.PSCustomObject]) {
        throw "Azure $Description parent was not a JSON object."
    }
    $property = $Object.PSObject.Properties[$Name]
    [int]$parsedValue = 0
    if ($null -eq $property -or -not [int]::TryParse(
            ([string]$property.Value).Trim(),
            [Globalization.NumberStyles]::Integer,
            [Globalization.CultureInfo]::InvariantCulture,
            [ref]$parsedValue
        )) {
        throw "Azure $Description response omitted integer property '$Name'."
    }
    return $parsedValue
}

function Get-CriteriaBenchAzureAccountSnapshot {
    [CmdletBinding()]
    param([Parameter(Mandatory)][string]$AzPath)

    $jsonOutput = Get-CriteriaBenchNativeOutput -FilePath $AzPath -ArgumentList @(
        "account", "show", "--output", "json", "--only-show-errors"
    ) -FailureMessage "Unable to validate the signed-in Azure account"
    $account = ConvertFrom-CriteriaBenchContainerAppsJsonObject `
        -Json ($jsonOutput -join [Environment]::NewLine) `
        -Description "account"
    $user = Get-CriteriaBenchContainerAppsRequiredObjectProperty `
        -Object $account -Name "user" -Description "account"
    return [pscustomobject]@{
        State = (Get-CriteriaBenchContainerAppsRequiredStringProperty `
                -Object $account -Name "state" -Description "account")
        UserName = (Get-CriteriaBenchContainerAppsRequiredStringProperty `
                -Object $user -Name "name" -Description "account user")
    }
}

function Get-CriteriaBenchContainerAppJobContract {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)][string]$AzPath,
        [Parameter(Mandatory)][string]$JobName,
        [Parameter(Mandatory)][string]$ResourceGroup
    )

    $jsonOutput = Get-CriteriaBenchNativeOutput -FilePath $AzPath -ArgumentList @(
        "containerapp", "job", "show", "--name", $JobName,
        "--resource-group", $ResourceGroup,
        "--output", "json", "--only-show-errors"
    ) -FailureMessage "Azure could not verify the deployed job contract"
    $job = ConvertFrom-CriteriaBenchContainerAppsJsonObject `
        -Json ($jsonOutput -join [Environment]::NewLine) `
        -Description "Container Apps job"
    $properties = Get-CriteriaBenchContainerAppsRequiredObjectProperty `
        -Object $job -Name "properties" -Description "Container Apps job"
    $configuration = Get-CriteriaBenchContainerAppsRequiredObjectProperty `
        -Object $properties -Name "configuration" -Description "Container Apps job properties"
    $manualTrigger = Get-CriteriaBenchContainerAppsRequiredObjectProperty `
        -Object $configuration -Name "manualTriggerConfig" `
        -Description "Container Apps job configuration"
    $template = Get-CriteriaBenchContainerAppsRequiredObjectProperty `
        -Object $properties -Name "template" -Description "Container Apps job properties"
    $containersProperty = $template.PSObject.Properties["containers"]
    if ($null -eq $containersProperty) {
        throw "Azure Container Apps job template omitted property 'containers'."
    }
    $containers = @(
        foreach ($container in $containersProperty.Value) {
            if ($null -eq $container -or
                $container -isnot [System.Management.Automation.PSCustomObject]) {
                throw "Azure Container Apps job template contained a non-object container."
            }
            $container
        }
    )
    if ($containers.Count -ne 1) {
        throw "Azure Container Apps job template did not contain exactly one container."
    }

    # Full-response paths are properties.configuration.* and
    # properties.template.containers[0].image; no Azure CLI projection is used.
    return [pscustomobject]@{
        TriggerType = (Get-CriteriaBenchContainerAppsRequiredStringProperty `
                -Object $configuration -Name "triggerType" -Description "Container Apps job configuration")
        ReplicaRetryLimit = (Get-CriteriaBenchContainerAppsRequiredIntProperty `
                -Object $configuration -Name "replicaRetryLimit" -Description "Container Apps job configuration")
        ReplicaTimeout = (Get-CriteriaBenchContainerAppsRequiredIntProperty `
                -Object $configuration -Name "replicaTimeout" -Description "Container Apps job configuration")
        Parallelism = (Get-CriteriaBenchContainerAppsRequiredIntProperty `
                -Object $manualTrigger -Name "parallelism" -Description "Container Apps manual trigger")
        ReplicaCompletionCount = (Get-CriteriaBenchContainerAppsRequiredIntProperty `
                -Object $manualTrigger -Name "replicaCompletionCount" -Description "Container Apps manual trigger")
        Name = (Get-CriteriaBenchContainerAppsRequiredStringProperty `
                -Object $containers[0] -Name "name" -Description "Container Apps job container")
        Image = (Get-CriteriaBenchContainerAppsRequiredStringProperty `
                -Object $containers[0] -Name "image" -Description "Container Apps job container")
    }
}

function Get-CriteriaBenchContainerAppJobExecutions {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)][string]$AzPath,
        [Parameter(Mandatory)][string]$JobName,
        [Parameter(Mandatory)][string]$ResourceGroup
    )

    $jsonOutput = Get-CriteriaBenchNativeOutput -FilePath $AzPath -ArgumentList @(
        "containerapp", "job", "execution", "list", "--name", $JobName,
        "--resource-group", $ResourceGroup,
        "--output", "json", "--only-show-errors"
    ) -FailureMessage "Azure could not verify the one-execution boundary"
    ConvertFrom-CriteriaBenchContainerAppsJsonObjectArray `
        -Json ($jsonOutput -join [Environment]::NewLine) `
        -Description "Container Apps job execution list"
}

function Start-CriteriaBenchContainerAppJobExecution {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)][string]$AzPath,
        [Parameter(Mandatory)][string]$JobName,
        [Parameter(Mandatory)][string]$ResourceGroup
    )

    $jsonOutput = Get-CriteriaBenchNativeOutput -FilePath $AzPath -ArgumentList @(
        "containerapp", "job", "start", "--name", $JobName,
        "--resource-group", $ResourceGroup,
        "--output", "json", "--only-show-errors"
    ) -FailureMessage "Azure could not start the single approved job execution"
    $execution = ConvertFrom-CriteriaBenchContainerAppsJsonObject `
        -Json ($jsonOutput -join [Environment]::NewLine) `
        -Description "Container Apps job start"
    $executionName = Get-CriteriaBenchContainerAppsRequiredStringProperty `
        -Object $execution -Name "name" -Description "Container Apps job start"
    if ($executionName -notmatch '^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$') {
        throw "Azure Container Apps job start returned an unsafe execution name."
    }
    return $executionName
}

function Get-CriteriaBenchContainerAppJobExecutionStatus {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)][string]$AzPath,
        [Parameter(Mandatory)][string]$JobName,
        [Parameter(Mandatory)][string]$ResourceGroup,
        [Parameter(Mandatory)]
        [ValidatePattern('^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$')]
        [string]$ExecutionName
    )

    $jsonOutput = Get-CriteriaBenchNativeOutput -FilePath $AzPath -ArgumentList @(
        "containerapp", "job", "execution", "show", "--name", $JobName,
        "--resource-group", $ResourceGroup,
        "--job-execution-name", $ExecutionName,
        "--output", "json", "--only-show-errors"
    ) -FailureMessage "Azure could not inspect the job execution"
    $execution = ConvertFrom-CriteriaBenchContainerAppsJsonObject `
        -Json ($jsonOutput -join [Environment]::NewLine) `
        -Description "Container Apps job execution"
    $properties = Get-CriteriaBenchContainerAppsRequiredObjectProperty `
        -Object $execution -Name "properties" -Description "Container Apps job execution"
    return (Get-CriteriaBenchContainerAppsRequiredStringProperty `
            -Object $properties -Name "status" -Description "Container Apps job execution properties")
}

function Get-CriteriaBenchDeletedKeyVaultExactMatchCount {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)][string]$AzPath,
        [Parameter(Mandatory)]
        [ValidatePattern('^[a-z][a-z0-9-]{1,22}[a-z0-9]$')]
        [string]$VaultName
    )

    $jsonOutput = Get-CriteriaBenchNativeOutput -FilePath $AzPath -ArgumentList @(
        "keyvault", "list-deleted", "--output", "json", "--only-show-errors"
    ) -FailureMessage "Azure could not inspect the soft-deleted project Key Vault"
    $deletedVaults = @(
        ConvertFrom-CriteriaBenchContainerAppsJsonObjectArray `
            -Json ($jsonOutput -join [Environment]::NewLine) `
            -Description "soft-deleted Key Vault list"
    )
    $matches = @(
        foreach ($deletedVault in $deletedVaults) {
            $candidateName = Get-CriteriaBenchContainerAppsRequiredStringProperty `
                -Object $deletedVault -Name "name" -Description "soft-deleted Key Vault"
            if ([string]::Equals(
                    $candidateName,
                    $VaultName,
                    [StringComparison]::OrdinalIgnoreCase
                )) {
                $deletedVault
            }
        }
    )
    return [int]$matches.Count
}

function Assert-CriteriaBenchContainerAppsPlan {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)][object]$Plan,
        [Parameter(Mandatory)][ValidateSet("base", "job")][string]$Stage
    )

    $changed = @(
        $Plan.resource_changes |
            Where-Object { $_.mode -eq "managed" -and @($_.change.actions) -notcontains "no-op" }
    )
    $expected = if ($Stage -eq "base") {
        @(
            "azurerm_resource_group.this[0]",
            "azurerm_log_analytics_workspace.this[0]",
            "azurerm_container_app_environment.this[0]",
            "azurerm_user_assigned_identity.job[0]",
            "azurerm_key_vault.this[0]",
            "azurerm_role_assignment.job_secret_reader[0]",
            "azurerm_role_assignment.operator_secret_writer[0]",
            "azurerm_consumption_budget_subscription.this[0]"
        )
    }
    else {
        @("azurerm_container_app_job.live[0]")
    }

    $addresses = @($changed | ForEach-Object { [string]$_.address } | Sort-Object)
    $expectedSorted = @($expected | Sort-Object)
    if (($addresses -join "`n") -ne ($expectedSorted -join "`n")) {
        throw "The $Stage plan contains resources outside the frozen allowlist."
    }
    foreach ($change in $changed) {
        if ((@($change.change.actions) -join ",") -ne "create") {
            throw "The $Stage plan contains an action other than a single create."
        }
    }
}

function Set-CriteriaBenchKeyVaultSecretInMemory {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)][string]$AzPath,
        [Parameter(Mandatory)][string]$VaultUri,
        [Parameter(Mandatory)][string]$ApiKey,
        [Parameter(Mandatory)][DateTimeOffset]$ExpiresAt
    )

    if ($VaultUri -notmatch '^https://[a-z0-9-]+\.vault\.azure\.net/$') {
        throw "The Key Vault URI is outside the expected Azure boundary."
    }
    if ($ApiKey -notmatch '^sk-[A-Za-z0-9_-]{20,}$') {
        throw "The ignored key does not have the expected OpenAI key shape."
    }

    $tokenOutput = Get-CriteriaBenchNativeOutput -FilePath $AzPath -ArgumentList @(
        "account", "get-access-token", "--resource", "https://vault.azure.net",
        "--query", "accessToken", "--output", "tsv", "--only-show-errors"
    ) -FailureMessage "Azure could not issue a Key Vault data-plane token"
    $accessToken = ([string]($tokenOutput -join "")).Trim()
    if ([string]::IsNullOrWhiteSpace($accessToken)) {
        throw "Azure returned an empty Key Vault access token."
    }

    $endpoint = $VaultUri + "secrets/openai-api-key?api-version=7.4"
    $body = @{
        value       = $ApiKey
        contentType = "OpenAI API key for one bounded CriteriaBench job"
        attributes  = @{ exp = $ExpiresAt.ToUnixTimeSeconds() }
    } | ConvertTo-Json -Compress -Depth 4
    $client = New-Object System.Net.Http.HttpClient
    try {
        for ($attempt = 1; $attempt -le 12; $attempt++) {
            $request = New-Object System.Net.Http.HttpRequestMessage(
                [System.Net.Http.HttpMethod]::Put,
                $endpoint
            )
            $content = New-Object System.Net.Http.StringContent(
                $body,
                [System.Text.Encoding]::UTF8,
                "application/json"
            )
            $request.Content = $content
            $request.Headers.Authorization = New-Object System.Net.Http.Headers.AuthenticationHeaderValue(
                "Bearer",
                $accessToken
            )
            try {
                $response = $client.SendAsync($request).GetAwaiter().GetResult()
                try {
                    if ($response.IsSuccessStatusCode) {
                        return
                    }
                    $status = [int]$response.StatusCode
                }
                finally {
                    $response.Dispose()
                }
            }
            finally {
                $content.Dispose()
                $request.Dispose()
            }
            if ($status -notin @(403, 408, 409, 429, 500, 502, 503, 504) -or $attempt -eq 12) {
                throw "The in-memory Key Vault import failed with HTTP status $status."
            }
            Start-Sleep -Seconds 5
        }
    }
    finally {
        $client.Dispose()
        $body = $null
        $accessToken = $null
        $ApiKey = $null
    }
}

function Get-CriteriaBenchSafeJobResult {
    [CmdletBinding()]
    param([Parameter(Mandatory)][string[]]$LogLines)

    $matches = @(
        $LogLines |
            Where-Object { $_ -match 'CRITERIABENCH_JOB_RESULT=(\{.*\})' } |
            ForEach-Object { $Matches[1] }
    )
    if ($matches.Count -ne 1) {
        throw "The job did not emit exactly one sanitized result marker."
    }
    $result = $matches[0] | ConvertFrom-Json
    $allowed = @(
        "status", "provider", "model", "paid", "evaluated_cases",
        "authorization_guard_usd", "projected_authorization_usd",
        "authorization_consumed_usd", "usage_priced_cost_usd",
        "max_attempts_per_case", "extraction_contract_sha256",
        "evaluation_contract_sha256", "fixture_sha256", "image_digest",
        "job_runner_sha256", "input_tokens", "output_tokens", "latency_ms",
        "inclusion_count", "exclusion_count", "schema_valid", "exact_match_f1",
        "token_f1", "macro_field_accuracy", "predicted_count", "reference_count",
        "error_type", "error_code", "error_details"
    )
    foreach ($property in $result.PSObject.Properties.Name) {
        if ($property -notin $allowed) {
            throw "The job result contains a field outside the sanitized evidence schema."
        }
    }
    return $result
}
