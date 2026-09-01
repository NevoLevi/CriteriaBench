from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SUPPORT = ROOT / "scripts" / "live-benchmark-support.ps1"
WRAPPER = ROOT / "scripts" / "run-live-benchmark.ps1"


def _powershell() -> str:
    executable = shutil.which("pwsh") or shutil.which("powershell")
    if executable is None:
        pytest.skip("PowerShell is required for the live-wrapper contract")
    return executable


def test_live_wrapper_is_explicit_frozen_and_uses_only_the_fixed_local_file() -> None:
    script = WRAPPER.read_text(encoding="utf-8")

    assert 'Join-Path $projectRoot ".env.local"' in script
    assert "EnvFilePath" not in script
    assert '"run", "--frozen", "--no-env-file", "criteriabench-benchmark"' in script
    assert '"--live", "--acknowledge-paid-api", "--budget-usd"' in script
    assert "ValidateRange(0.01, 2.0)" in script
    assert "OPENAI_API_KEY                     = $apiKey" in script
    assert 'CRITERIABENCH_OPENAI_MAX_RETRIES   = "0"' in script
    assert script.count("CRITERIABENCH_OPENAI_MAX_RETRIES") == 1
    assert "$liveEnvironment.Clear()" in script
    assert "$apiKey = $null" in script


def test_support_reads_fake_temp_key_without_printing_and_restores_env(
    tmp_path: Path,
) -> None:
    fake_key = "fake-test-key-never-real"
    fake_env = tmp_path / ".env.local"
    fake_env.write_text(
        f'IGNORED_VALUE=not-used\nOPENAI_API_KEY="{fake_key}"\n',
        encoding="utf-8",
    )
    exercise = tmp_path / "exercise.ps1"
    exercise.write_text(
        """
param([string]$SupportPath, [string]$FakeEnvPath)
$ErrorActionPreference = "Stop"
. $SupportPath
$prior = [Environment]::GetEnvironmentVariable("OPENAI_API_KEY", "Process")
$key = Read-CriteriaBenchOpenAIKey -LiteralPath $FakeEnvPath
$observed = $null
Invoke-CriteriaBenchScopedEnvironment -Variables @{ OPENAI_API_KEY = $key } -Action {
    $script:observed = $env:OPENAI_API_KEY
} | Out-Null
if ($observed -ne $key) { throw "Child scope did not receive the key." }
$after = [Environment]::GetEnvironmentVariable("OPENAI_API_KEY", "Process")
if ($after -ne $prior) { throw "The prior process environment was not restored." }
$key = $null
""",
        encoding="utf-8",
    )

    environment = os.environ.copy()
    environment["OPENAI_API_KEY"] = "fake-prior-value"
    result = subprocess.run(
        [
            _powershell(),
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(exercise),
            str(SUPPORT),
            str(fake_env),
        ],
        cwd=ROOT,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert fake_key not in result.stdout
    assert fake_key not in result.stderr
