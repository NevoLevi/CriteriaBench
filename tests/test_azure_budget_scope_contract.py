from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_budget_covers_parent_and_aks_managed_resource_groups() -> None:
    terraform = (ROOT / "infra" / "azure" / "combined-budget.tf").read_text(encoding="utf-8")

    assert 'resource "azurerm_consumption_budget_subscription"' in terraform
    assert 'name     = "ResourceGroupName"' in terraform
    assert "azurerm_resource_group.this[0].name" in terraform
    assert "var.node_resource_group_name" in terraform


def test_destroy_verifies_both_azure_resource_groups() -> None:
    script = (ROOT / "scripts" / "azure-destroy.ps1").read_text(encoding="utf-8")

    assert "resource_group_name" in script
    assert "node_resource_group_name" in script
