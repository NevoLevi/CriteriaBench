variable "confirm_billable_deployment" {
  description = "Explicit gate; false creates no Azure resources."
  type        = bool
  default     = false
}

variable "secret_ready" {
  description = "Second-stage gate set only after the out-of-state Key Vault import succeeds."
  type        = bool
  default     = false

  validation {
    condition     = !var.secret_ready || var.confirm_billable_deployment
    error_message = "secret_ready requires confirm_billable_deployment=true."
  }
}

variable "location" {
  type    = string
  default = "Germany West Central"

  validation {
    condition     = var.location == "Germany West Central"
    error_message = "The production proof is restricted to Germany West Central."
  }
}

variable "resource_group_name" {
  type    = string
  default = "rg-criteriabench-prod-demo"

  validation {
    condition     = var.resource_group_name == "rg-criteriabench-prod-demo"
    error_message = "Only the reviewed production-proof resource group is allowed."
  }
}

variable "image_digest" {
  description = "Reviewed immutable GHCR digest without a mutable tag."
  type        = string

  validation {
    condition     = can(regex("^sha256:[0-9a-f]{64}$", var.image_digest))
    error_message = "image_digest must be a lowercase sha256 digest."
  }
}

variable "budget_amount" {
  description = "Monthly Azure alert amount in subscription billing currency; not a hard cap."
  type        = number
  default     = 15

  validation {
    condition     = var.budget_amount > 0 && var.budget_amount <= 15
    error_message = "The production proof budget alert cannot exceed 15 billing-currency units."
  }
}

variable "budget_contact_email" {
  description = "Budget notification address; retained only in ignored local Terraform state."
  type        = string
  sensitive   = true

  validation {
    condition     = can(regex("^[^@\\s]+@[^@\\s]+\\.[^@\\s]+$", var.budget_contact_email))
    error_message = "A valid budget notification email is required."
  }
}

variable "budget_start_date" {
  description = "Frozen UTC budget period start in exact YYYY-MM-01T00:00:00Z form."
  type        = string

  validation {
    condition     = can(regex("^[0-9]{4}-(0[1-9]|1[0-2])-01T00:00:00Z$", var.budget_start_date))
    error_message = "budget_start_date must use exact YYYY-MM-01T00:00:00Z form."
  }
}

variable "review_at_utc" {
  description = "Operator review-by timestamp in RFC3339 form; advisory only and does not automatically tear down resources."
  type        = string

  validation {
    condition = !var.confirm_billable_deployment || (
      can(timecmp(var.review_at_utc, timestamp())) &&
      timecmp(var.review_at_utc, timestamp()) > 0 &&
      timecmp(var.review_at_utc, timeadd(timestamp(), "720h5m")) <= 0
    )
    error_message = "An enabled production proof needs a future operator review-by timestamp no more than 30 days away; it does not trigger automatic teardown."
  }
}

variable "owner_tag" {
  type    = string
  default = "nevo-levi"
}
