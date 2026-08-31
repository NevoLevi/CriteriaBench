Set-StrictMode -Version Latest

function Read-CriteriaBenchOpenAIKey {
    [CmdletBinding()]
    param([Parameter(Mandatory)][string]$LiteralPath)

    if (-not (Test-Path -LiteralPath $LiteralPath -PathType Leaf)) {
        throw "The ignored .env.local file is missing."
    }

    $apiKey = $null
    $reader = [System.IO.File]::OpenText($LiteralPath)
    try {
        while ($null -ne ($line = $reader.ReadLine())) {
            if ($line -notmatch '^\s*OPENAI_API_KEY\s*=\s*(.*?)\s*$') {
                continue
            }
            if ($null -ne $apiKey) {
                throw ".env.local contains more than one OPENAI_API_KEY assignment."
            }

            $candidate = $Matches[1].Trim()
            if ($candidate.Length -ge 2) {
                $first = $candidate[0]
                $last = $candidate[$candidate.Length - 1]
                if (($first -eq '"' -and $last -eq '"') -or
                    ($first -eq "'" -and $last -eq "'")) {
                    $candidate = $candidate.Substring(1, $candidate.Length - 2)
                }
                elseif ($first -eq '"' -or $first -eq "'" -or
                    $last -eq '"' -or $last -eq "'") {
                    throw "OPENAI_API_KEY has unmatched quotes in .env.local."
                }
            }
            if ([string]::IsNullOrWhiteSpace($candidate)) {
                throw "OPENAI_API_KEY is empty in .env.local."
            }
            $apiKey = $candidate
        }
    }
    finally {
        $reader.Dispose()
    }

    if ($null -eq $apiKey) {
        throw ".env.local does not contain OPENAI_API_KEY."
    }
    return $apiKey
}

function Invoke-CriteriaBenchScopedEnvironment {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)][hashtable]$Variables,
        [Parameter(Mandatory)][scriptblock]$Action
    )

    $previous = @{}
    try {
        foreach ($name in $Variables.Keys) {
            if ($name -notmatch '^[A-Z][A-Z0-9_]*$') {
                throw "Invalid scoped environment variable name."
            }
            $previous[$name] = [Environment]::GetEnvironmentVariable($name, "Process")
            [Environment]::SetEnvironmentVariable(
                $name, [string]$Variables[$name], "Process"
            )
        }
        & $Action
    }
    finally {
        foreach ($name in $Variables.Keys) {
            [Environment]::SetEnvironmentVariable($name, $previous[$name], "Process")
        }
    }
}
