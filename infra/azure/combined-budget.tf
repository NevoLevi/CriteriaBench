# One billing-currency alert boundary spans the parent and AKS managed-node
# resource groups. Azure budgets notify after delayed cost ingestion; they do
# not stop resources or authorize spending up to the amount.
data "azurerm_subscription" "current" {}

resource "azurerm_consumption_budget_subscription" "criteriabench_combined" {
  count = local.enabled ? 1 : 0

  name            = "criteriabench-demo-combined-budget"
  subscription_id = data.azurerm_subscription.current.id
  amount          = var.budget_amount
  time_grain      = "Monthly"

  time_period {
    start_date = formatdate("YYYY-MM-01'T'00:00:00Z", timestamp())
  }

  filter {
    dimension {
      name     = "ResourceGroupName"
      operator = "In"
      values = [
        azurerm_resource_group.this[0].name,
        var.node_resource_group_name,
      ]
    }
  }

  notification {
    enabled        = true
    threshold      = 50
    operator       = "GreaterThanOrEqualTo"
    threshold_type = "Actual"
    contact_emails = var.budget_contact_emails
  }

  notification {
    enabled        = true
    threshold      = 80
    operator       = "GreaterThanOrEqualTo"
    threshold_type = "Actual"
    contact_emails = var.budget_contact_emails
  }

  notification {
    enabled        = true
    threshold      = 100
    operator       = "GreaterThanOrEqualTo"
    threshold_type = "Actual"
    contact_emails = var.budget_contact_emails
  }

  notification {
    enabled        = true
    threshold      = 100
    operator       = "GreaterThanOrEqualTo"
    threshold_type = "Forecasted"
    contact_emails = var.budget_contact_emails
  }
}
