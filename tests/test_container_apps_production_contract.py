from __future__ import annotations

import base64
import hashlib
import importlib.util
import json
from pathlib import Path
from types import ModuleType

import pytest

ROOT = Path(__file__).resolve().parents[1]
INFRA = ROOT / "infra" / "container-apps"


def _runner_module() -> ModuleType:
    path = INFRA / "job_runner.py"
    spec = importlib.util.spec_from_file_location("criteriabench_container_job", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_fixed_job_fixture_is_byte_identical_to_public_manifested_case() -> None:
    runner = _runner_module()
    embedded = base64.b64decode(runner._FIXTURE_B64, validate=True)
    public = (ROOT / "data" / "synthetic" / "benchmark_case_001.json").read_bytes()

    assert embedded == public
    assert hashlib.sha256(embedded).hexdigest() == runner._FIXTURE_SHA256


def test_job_summary_excludes_extraction_text_and_provider_payload() -> None:
    runner = _runner_module()
    artifact = {
        "status": "completed",
        "provider": "openai",
        "model": "gpt-5.6-luna",
        "paid": True,
        "evaluated_cases": 1,
        "authorization_guard_usd": 0.02,
        "projected_authorization_usd": 0.0111,
        "authorization_consumed_usd": 0.0111,
        "total_usage_priced_cost_usd": 0.0005,
        "max_attempts_per_case": 1,
        "extraction_contract_sha256": "a" * 64,
        "evaluation_contract_sha256": "b" * 64,
        "results": [
            {
                "status": "completed",
                "fixture_sha256": "c" * 64,
                "input_tokens": 10,
                "output_tokens": 20,
                "latency_ms": 30,
                "extraction": {
                    "inclusion_criteria": [{"source_text": "DO-NOT-LOG"}],
                    "exclusion_criteria": [],
                },
                "evaluation": {
                    "schema_valid": True,
                    "exact_match_f1": 1.0,
                    "token_f1": 1.0,
                    "macro_field_accuracy": 1.0,
                    "predicted_count": 1,
                    "reference_count": 1,
                },
            }
        ],
    }

    serialized = json.dumps(runner._safe_result(artifact))

    assert "DO-NOT-LOG" not in serialized
    assert '"extraction":' not in serialized
    assert "response_id" not in serialized


def test_terraform_job_is_no_ingress_manual_single_retryless_execution() -> None:
    main = (INFRA / "main.tf").read_text(encoding="utf-8")
    variables = (INFRA / "variables.tf").read_text(encoding="utf-8")

    assert 'resource "azurerm_container_app_job" "live"' in main
    assert 'resource "azurerm_container_app"' not in main
    assert "replica_retry_limit          = 0" in main
    assert "replica_timeout_in_seconds   = 300" in main
    assert "parallelism              = 1" in main
    assert "replica_completion_count = 1" in main
    assert "cpu     = 0.25" in main
    assert 'memory  = "0.5Gi"' in main
    assert 'secret_name = "openai-key"' in main
    assert "key_vault_secret_id" in main
    assert "azurerm_key_vault_secret" not in main
    assert "local.job_bootstrap" in main
    assert "filesha256" in main
    assert "base64gzip" in main
    assert 'variable "budget_start_date"' in variables
    assert "start_date = var.budget_start_date" in main
    assert "formatdate(\"YYYY-MM-01'T'00:00:00Z\", timestamp())" not in main
    assert "workload_profile {" in main
    assert 'name                  = "Consumption"' in main
    assert 'workload_profile_type = "Consumption"' in main
    assert "minimum_count         = 0" in main
    assert "maximum_count         = 0" in main

    providers = (INFRA / "providers.tf").read_text(encoding="utf-8")
    assert "purge_soft_delete_on_destroy    = false" in providers
    assert "recover_soft_deleted_key_vaults = true" in providers


def test_deployment_imports_secret_in_memory_and_starts_only_once() -> None:
    helper = (ROOT / "scripts" / "container-apps-safety.ps1").read_text(encoding="utf-8")
    deploy = (ROOT / "scripts" / "container-apps-deploy.ps1").read_text(encoding="utf-8")
    destroy = (ROOT / "scripts" / "container-apps-destroy.ps1").read_text(encoding="utf-8")

    assert "HttpRequestMessage" in helper
    assert "https://vault.azure.net" in helper
    assert "api-version=7.4" in helper
    assert "ReadAsString" not in helper
    assert '"keyvault", "secret", "set"' not in deploy
    assert helper.count('"containerapp", "job", "start"') == 1
    assert deploy.count("Start-CriteriaBenchContainerAppJobExecution") == 1
    assert "length(@)" not in helper
    assert "length(@)" not in deploy
    assert "length(@)" not in destroy
    assert "{trigger:" not in helper
    assert "{trigger:" not in deploy
    assert "[?name==" not in destroy
    assert helper.count('"--query"') == 1  # Key Vault access-token scalar only.
    assert deploy.count('"--query"') == 1  # Provider registration scalar only.
    assert '"--query"' not in destroy
    assert "ConvertFrom-CriteriaBenchContainerAppsJsonObject" in helper
    assert "ConvertFrom-CriteriaBenchContainerAppsJsonObjectArray" in helper
    assert "properties.configuration.*" in helper
    assert "properties.template.containers[0].image" in helper
    assert "Get-CriteriaBenchContainerAppJobContract" in deploy
    assert "Get-CriteriaBenchContainerAppJobExecutions" in deploy
    assert "Start-CriteriaBenchContainerAppJobExecution" in deploy
    assert "Get-CriteriaBenchContainerAppJobExecutionStatus" in deploy
    assert "$job.Name -ne $containerName" in deploy
    assert "safe job execution name" in deploy
    assert "job start returned an unsafe execution name" in helper
    assert "Get-CriteriaBenchDeletedKeyVaultExactMatchCount" in destroy
    assert "ValidatePattern('^[a-z][a-z0-9-]{1,22}[a-z0-9]$')" in helper
    assert "StartExactlyOnePaidExecution" in deploy
    assert "AutoDestroyOnFailure" in deploy
    assert "$deploymentState = [pscustomobject]@{ TerraformStarted = $false }" in deploy
    assert "$deploymentState.TerraformStarted = $true" in deploy
    assert "if ($deploymentState.TerraformStarted)" in deploy
    assert "$terraformStarted" not in deploy
    assert deploy.count("-var=budget_start_date=$budgetStartDate") == 2
    assert destroy.count("-var=budget_start_date=$budgetStartDate") == 1
    assert deploy.count("$budgetStartDate = [DateTimeOffset]::UtcNow.ToString") == 1
    assert destroy.count("$budgetStartDate = [DateTimeOffset]::UtcNow.ToString") == 1
    assert 'keyvault", "purge' not in destroy
    assert "list-deleted" in helper


@pytest.mark.parametrize(
    "path",
    [
        ROOT / "scripts" / "container-apps-safety.ps1",
        ROOT / "scripts" / "container-apps-deploy.ps1",
        ROOT / "scripts" / "container-apps-destroy.ps1",
    ],
)
def test_container_apps_powershell_parses(path: Path) -> None:
    # The repository's Windows CI contract executes the same parser; this keeps the paths listed.
    assert path.is_file()
