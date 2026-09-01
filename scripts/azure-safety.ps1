Set-StrictMode -Version Latest

function Invoke-CriteriaBenchNative {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)][string]$FilePath,
        [Parameter()][string[]]$ArgumentList = @(),
        [Parameter(Mandatory)][string]$FailureMessage
    )

    & $FilePath @ArgumentList
    if ($LASTEXITCODE -ne 0) {
        throw "$FailureMessage (exit code $LASTEXITCODE)."
    }
}

function Get-CriteriaBenchNativeOutput {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)][string]$FilePath,
        [Parameter()][string[]]$ArgumentList = @(),
        [Parameter(Mandatory)][string]$FailureMessage
    )

    $output = & $FilePath @ArgumentList
    if ($LASTEXITCODE -ne 0) {
        throw "$FailureMessage (exit code $LASTEXITCODE)."
    }
    return $output
}

function Write-CriteriaBenchUtf8NoBom {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)][string]$LiteralPath,
        [Parameter(Mandatory)][string]$Content
    )

    $encoding = New-Object System.Text.UTF8Encoding($false)
    [IO.File]::WriteAllText($LiteralPath, $Content, $encoding)
}

function Assert-CriteriaBenchChildPath {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)][string]$ProjectRoot,
        [Parameter(Mandatory)][string]$TargetPath
    )

    $resolvedProjectRoot = (Resolve-Path -LiteralPath $ProjectRoot).Path.TrimEnd('\', '/')
    $resolvedTarget = (Resolve-Path -LiteralPath $TargetPath).Path
    $expectedPrefix = $resolvedProjectRoot + [IO.Path]::DirectorySeparatorChar
    if (-not $resolvedTarget.StartsWith($expectedPrefix, [StringComparison]::OrdinalIgnoreCase)) {
        throw "Refusing an infrastructure operation outside the CriteriaBench project."
    }
    return $resolvedTarget
}

function Get-CriteriaBenchAzureUsageName {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)][object]$UsageRecord
    )

    $nameProperty = $UsageRecord.PSObject.Properties["name"]
    if ($null -eq $nameProperty) {
        throw "Azure returned a quota record without a machine-readable name. Terraform was not run."
    }
    $nameValue = $nameProperty.Value
    if ($nameValue -is [string]) {
        $normalized = ([string]$nameValue).Trim()
    }
    else {
        $valueProperty = $nameValue.PSObject.Properties["value"]
        if ($null -eq $valueProperty) {
            throw "Azure returned a quota record without name.value. Terraform was not run."
        }
        $normalized = ([string]$valueProperty.Value).Trim()
    }
    if ([string]::IsNullOrWhiteSpace($normalized)) {
        throw "Azure returned an empty quota name. Terraform was not run."
    }
    return $normalized
}

function Get-CriteriaBenchAzureQuotaSnapshot {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)][AllowEmptyCollection()][object[]]$UsageRecords,
        [Parameter(Mandatory)][string]$QuotaName,
        [Parameter(Mandatory)][string]$QuotaLabel
    )

    $matches = @(
        foreach ($usageRecord in $UsageRecords) {
            $name = Get-CriteriaBenchAzureUsageName -UsageRecord $usageRecord
            if ([string]::Equals($name, $QuotaName, [StringComparison]::OrdinalIgnoreCase)) {
                $usageRecord
            }
        }
    )
    if ($matches.Count -ne 1) {
        throw "Azure returned $($matches.Count) exact $QuotaLabel quota records; exactly one is required. Terraform was not run."
    }

    $currentProperty = $matches[0].PSObject.Properties["currentValue"]
    $limitProperty = $matches[0].PSObject.Properties["limit"]
    if ($null -eq $currentProperty -or $null -eq $limitProperty) {
        throw "Azure returned an incomplete $QuotaLabel quota record. Terraform was not run."
    }

    [long]$currentValue = 0
    [long]$limit = 0
    $currentValid = [long]::TryParse(
        ([string]$currentProperty.Value).Trim(),
        [Globalization.NumberStyles]::Integer,
        [Globalization.CultureInfo]::InvariantCulture,
        [ref]$currentValue
    )
    $limitValid = [long]::TryParse(
        ([string]$limitProperty.Value).Trim(),
        [Globalization.NumberStyles]::Integer,
        [Globalization.CultureInfo]::InvariantCulture,
        [ref]$limit
    )
    if (-not $currentValid -or -not $limitValid -or $currentValue -lt 0 -or $limit -lt 0 -or $currentValue -gt $limit) {
        throw "Azure returned invalid current/limit values for $QuotaLabel quota. Terraform was not run."
    }

    return [pscustomobject]@{
        Name      = $QuotaName
        Label     = $QuotaLabel
        Current   = $currentValue
        Limit     = $limit
        Remaining = $limit - $currentValue
    }
}

function Assert-CriteriaBenchAzureVmQuota {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)][string]$VmSize,
        [Parameter(Mandatory)][AllowEmptyCollection()][object[]]$SkuCandidates,
        [Parameter(Mandatory)][AllowEmptyCollection()][object[]]$UsageRecords,
        [ValidateRange(1, 1)][int]$NodeCount = 1
    )

    $matchingSkus = @(
        $SkuCandidates | Where-Object {
            $nameProperty = $_.PSObject.Properties["name"]
            $null -ne $nameProperty -and
                [string]::Equals(
                    ([string]$nameProperty.Value).Trim(),
                    $VmSize,
                    [StringComparison]::OrdinalIgnoreCase
                )
        }
    )
    if ($matchingSkus.Count -ne 1) {
        throw "Azure returned $($matchingSkus.Count) exact matches for SKU '$VmSize'; exactly one is required. Terraform was not run."
    }
    $sku = $matchingSkus[0]

    $restrictionsProperty = $sku.PSObject.Properties["restrictions"]
    if ($null -eq $restrictionsProperty) {
        throw "Azure omitted restriction metadata for SKU '$VmSize'. Terraform was not run."
    }
    if (@($restrictionsProperty.Value).Count -gt 0) {
        $reasonCodes = @(
            $restrictionsProperty.Value | ForEach-Object {
                $reasonProperty = $_.PSObject.Properties["reasonCode"]
                if ($null -eq $reasonProperty) { "unknown" } else { [string]$reasonProperty.Value }
            }
        ) -join ", "
        throw "SKU '$VmSize' is restricted for this subscription ($reasonCodes). Terraform was not run."
    }

    $familyProperty = $sku.PSObject.Properties["family"]
    if ($null -eq $familyProperty -or [string]::IsNullOrWhiteSpace([string]$familyProperty.Value)) {
        throw "Azure omitted the quota family for SKU '$VmSize'. Terraform was not run."
    }
    $family = ([string]$familyProperty.Value).Trim()

    $vcpusProperty = $sku.PSObject.Properties["vcpus"]
    $vcpusValues = @()
    if ($null -ne $vcpusProperty) {
        $vcpusValues = @($vcpusProperty.Value)
    }
    if ($vcpusValues.Count -ne 1) {
        throw "Azure returned $($vcpusValues.Count) vCPU capabilities for SKU '$VmSize'; exactly one is required. Terraform was not run."
    }
    [long]$vcpusPerNode = 0
    $vcpusValid = [long]::TryParse(
        ([string]$vcpusValues[0]).Trim(),
        [Globalization.NumberStyles]::Integer,
        [Globalization.CultureInfo]::InvariantCulture,
        [ref]$vcpusPerNode
    )
    if (-not $vcpusValid -or $vcpusPerNode -le 0) {
        throw "Azure returned an invalid vCPU capability for SKU '$VmSize'. Terraform was not run."
    }

    $requiredVcpus = $vcpusPerNode * $NodeCount
    $familyQuota = Get-CriteriaBenchAzureQuotaSnapshot `
        -UsageRecords $UsageRecords `
        -QuotaName $family `
        -QuotaLabel "VM-family"
    $regionalQuota = Get-CriteriaBenchAzureQuotaSnapshot `
        -UsageRecords $UsageRecords `
        -QuotaName "cores" `
        -QuotaLabel "total regional vCPU"

    foreach ($quota in @($familyQuota, $regionalQuota)) {
        if ($quota.Remaining -lt $requiredVcpus) {
            throw "Insufficient $($quota.Label) quota for '$VmSize' in family '$family': required $requiredVcpus vCPUs, current $($quota.Current), limit $($quota.Limit), remaining $($quota.Remaining). Terraform was not run."
        }
    }

    return [pscustomobject]@{
        VmSize            = $VmSize
        Family            = $family
        VcpusPerNode      = $vcpusPerNode
        NodeCount         = $NodeCount
        RequiredVcpus     = $requiredVcpus
        FamilyCurrent     = $familyQuota.Current
        FamilyLimit       = $familyQuota.Limit
        FamilyRemaining   = $familyQuota.Remaining
        RegionalCurrent   = $regionalQuota.Current
        RegionalLimit     = $regionalQuota.Limit
        RegionalRemaining = $regionalQuota.Remaining
    }
}

function Get-CriteriaBenchTerraformManagedStateEntry {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)][AllowEmptyCollection()][string[]]$Address
    )

    $dataSourceAddressPattern = '^(?:module\.[^.\[\]\s]+(?:\[(?:[0-9]+|"(?:[^"\\]|\\.)*")\])?\.)*data\.[^.\[\]\s]+\.[^.\[\]\s]+(?:\[(?:[0-9]+|"(?:[^"\\]|\\.)*")\])?$'
    foreach ($stateAddress in $Address) {
        $normalizedAddress = ([string]$stateAddress).Trim()
        if ([string]::IsNullOrWhiteSpace($normalizedAddress)) {
            continue
        }
        if (-not [regex]::IsMatch(
                $normalizedAddress,
                $dataSourceAddressPattern,
                [Text.RegularExpressions.RegexOptions]::CultureInvariant
            )) {
            $normalizedAddress
        }
    }
}
