Set-StrictMode -Version Latest

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


