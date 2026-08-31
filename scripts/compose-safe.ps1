[CmdletBinding()]
param(
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]] $ComposeArgs
)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
$composePath = Join-Path $projectRoot "compose.yaml"
if (-not (Test-Path -LiteralPath $composePath -PathType Leaf)) {
    throw "The project Compose definition was not found."
}

$unsupportedOverride = $ComposeArgs | Where-Object {
    $_ -eq "--env-file" -or $_ -like "--env-file=*" -or
    $_ -eq "--file" -or $_ -like "--file=*" -or
    $_ -eq "-f" -or $_ -like "-f=*"
}
if ($unsupportedOverride) {
    throw "Explicit Compose file/env-file overrides are not supported by this project wrapper."
}

$previousDisableEnvFile = [Environment]::GetEnvironmentVariable(
    "COMPOSE_DISABLE_ENV_FILE", "Process"
)
$previousComposeEnvFiles = [Environment]::GetEnvironmentVariable(
    "COMPOSE_ENV_FILES", "Process"
)
$previousComposeFile = [Environment]::GetEnvironmentVariable("COMPOSE_FILE", "Process")
try {
    $env:COMPOSE_DISABLE_ENV_FILE = "1"
    [Environment]::SetEnvironmentVariable("COMPOSE_ENV_FILES", $null, "Process")
    [Environment]::SetEnvironmentVariable("COMPOSE_FILE", $null, "Process")

    & docker compose --file $composePath --project-name criteriabench @ComposeArgs
    if ($LASTEXITCODE -ne 0) {
        throw "docker compose exited with code $LASTEXITCODE"
    }
}
finally {
    [Environment]::SetEnvironmentVariable(
        "COMPOSE_DISABLE_ENV_FILE", $previousDisableEnvFile, "Process"
    )
    [Environment]::SetEnvironmentVariable(
        "COMPOSE_ENV_FILES", $previousComposeEnvFiles, "Process"
    )
    [Environment]::SetEnvironmentVariable("COMPOSE_FILE", $previousComposeFile, "Process")
}
