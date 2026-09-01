Set-StrictMode -Version Latest

function ConvertFrom-CriteriaBenchJsonObjectArray {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)][AllowEmptyString()][string]$Json,
        [Parameter(Mandatory)][string]$Description
    )

    $trimmedJson = $Json.Trim()
    if (-not $trimmedJson.StartsWith("[", [StringComparison]::Ordinal) -or
        -not $trimmedJson.EndsWith("]", [StringComparison]::Ordinal)) {
        throw "Azure $Description response was not a JSON array. Terraform was not run."
    }

    try {
        $parsed = $trimmedJson | ConvertFrom-Json -ErrorAction Stop
    }
    catch {
        throw "Azure $Description response was not valid JSON. Terraform was not run."
    }

    foreach ($item in $parsed) {
        if ($null -eq $item -or $item -isnot [System.Management.Automation.PSCustomObject]) {
            throw "Azure $Description response contained a non-object array item. Terraform was not run."
        }
        $item
    }
}
