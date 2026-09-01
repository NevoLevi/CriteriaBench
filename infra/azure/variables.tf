variable "confirm_billable_deployment" {
  description = "Explicit safety gate. Unless true, Terraform creates no Azure resources."
  type        = bool
  default     = false
}

variable "resource_group_name" {
  description = "Dedicated resource group for the disposable CriteriaBench demo."
  type        = string
  default     = "rg-criteriabench-demo"

  validation {
    condition     = startswith(var.resource_group_name, "rg-criteriabench-")
    error_message = "The resource group name must start with rg-criteriabench-."
  }
}

variable "location" {
  description = "Azure region for the demo."
  type        = string
  default     = "Germany West Central"

  validation {
    condition     = var.location == "Germany West Central"
    error_message = "This portfolio deployment is intentionally restricted to Germany West Central."
  }
}

variable "cluster_name" {
  description = "AKS cluster name."
  type        = string
  default     = "aks-criteriabench-demo"
}

variable "node_vm_size" {
  description = "AKS system-pool VM. The single reviewed size has two vCPUs and eight GB; preflight checks its live family and regional quota."
  type        = string
  default     = "Standard_D2as_v4"

  validation {
    condition     = var.node_vm_size == "Standard_D2as_v4"
    error_message = "This cost-bounded demo currently permits only the reviewed Standard_D2as_v4 SKU."
  }
}

variable "deployment_ttl_hours" {
  description = "Maximum intended lifetime of the billable demo. Used for validation and tagging."
  type        = number
  default     = 8

  validation {
    condition     = var.deployment_ttl_hours >= 1 && var.deployment_ttl_hours <= 8
    error_message = "The disposable deployment lifetime must be between one and eight hours."
  }
}

variable "expires_at_utc" {
  description = "RFC3339 UTC teardown deadline, computed by the deployment script."
  type        = string
  default     = ""

  validation {
    condition = !var.confirm_billable_deployment || (
      can(timecmp(var.expires_at_utc, timestamp())) &&
      timecmp(var.expires_at_utc, timestamp()) > 0 &&
      timecmp(var.expires_at_utc, timeadd(timestamp(), "8h5m")) <= 0
    )
    error_message = "When deployment is enabled, expires_at_utc must be in the future and no more than about eight hours away."
  }
}

variable "budget_amount" {
  description = "Monthly Azure alert budget in the subscription billing currency. Budgets alert; they do not stop resources."
  type        = number
  default     = 15

  validation {
    condition     = var.budget_amount > 0 && var.budget_amount <= 15
    error_message = "This project never authorizes a budget value above 15 billing-currency units."
  }
}

variable "budget_contact_emails" {
  description = "Addresses that receive cost alerts. Stored in Terraform state; keep state local and protected."
  type        = list(string)
  default     = []
  sensitive   = true

  validation {
    condition = !var.confirm_billable_deployment || (
      length(var.budget_contact_emails) > 0 &&
      alltrue([for email in var.budget_contact_emails : can(regex("^[^@\\s]+@[^@\\s]+\\.[^@\\s]+$", email))])
    )
    error_message = "At least one valid budget email is required for a billable deployment."
  }
}

variable "owner_tag" {
  description = "Non-secret owner label for inventory."
  type        = string
  default     = "nevo-levi"
}
