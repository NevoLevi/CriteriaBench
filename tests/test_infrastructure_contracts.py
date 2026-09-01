"""Cheap regression checks for the portfolio infrastructure safety contract."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _read(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def test_container_is_nonroot_and_health_checked() -> None:
    dockerfile = _read("Dockerfile")
    assert "USER app" in dockerfile
    assert "/healthz" in dockerfile
    assert "LLM_PROVIDER=mock" in dockerfile


def test_secret_files_and_terraform_artifacts_are_excluded() -> None:
    gitignore = _read(".gitignore")
    dockerignore = _read(".dockerignore")
    for content in (gitignore, dockerignore):
        assert ".env" in content
        assert ".env.*" in content
        assert ".terraform" in content
        assert ".tfstate" in content
        assert ".criteriabench.tfplan" in content


def test_routine_compose_never_loads_a_live_key() -> None:
    compose = _read("compose.yaml")
    assert "LLM_PROVIDER: mock" in compose
    assert 'ALLOW_PAID_CALLS: "false"' in compose
    assert "OPENAI_API_KEY" not in compose
    assert "env_file:" not in compose


def test_kubernetes_probes_and_worker_update_strategy() -> None:
    api = _read("deploy/helm/criteriabench/templates/api-deployment.yaml")
    worker = _read("deploy/helm/criteriabench/templates/worker-deployment.yaml")
    assert "/healthz" in api
    assert "/readyz" in api
    assert "type: Recreate" in worker


def test_cloud_chart_has_no_checked_in_database_password() -> None:
    values = _read("deploy/helm/criteriabench/values.yaml")
    base_config = _read("deploy/k8s/base/configmap.yaml")
    assert "password: criteriabench" not in values
    assert "postgresql+asyncpg://criteriabench:criteriabench" not in base_config
    assert "OPENAI_API_KEY" not in values


def test_azure_stack_is_small_gated_and_modern() -> None:
    variables = _read("infra/azure/variables.tf")
    main = _read("infra/azure/main.tf")
    combined_budget = _read("infra/azure/combined-budget.tf")
    assert "default     = false" in variables
    assert "var.budget_amount <= 15" in variables
    assert "sku_tier" in main and '"Free"' in main
    assert "node_count" in main and "= 1" in main
    assert 'network_plugin      = "azure"' in main
    assert 'network_plugin_mode = "overlay"' in main
    assert 'network_data_plane  = "cilium"' in main
    assert "azurerm_consumption_budget_subscription" in combined_budget
    assert "ResourceGroupName" in combined_budget
    assert "azurerm_container_registry" not in main


def test_cloud_apply_requires_reviewed_plan_and_immutable_image() -> None:
    apply_script = _read("scripts/azure-apply-reviewed.ps1")
    assert "ReviewedPlanSha256" in apply_script
    assert 'ValidatePattern("^sha256:' in apply_script
    assert "AutoDestroyOnFailure" in apply_script
    assert '"imagetools"' in apply_script
    assert '"inspect"' in apply_script
    assert "image.digest" in apply_script


def test_ci_sarif_scan_blocks_only_requested_severities() -> None:
    workflow = _read(".github/workflows/ci.yml")
    assert "format: sarif" in workflow
    assert "severity: CRITICAL,HIGH" in workflow
    assert "limit-severities-for-sarif: true" in workflow
    assert 'exit-code: "1"' in workflow
