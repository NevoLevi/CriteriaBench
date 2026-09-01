locals {
  enabled        = var.confirm_billable_deployment
  job_enabled    = local.enabled && var.secret_ready
  key_vault_name = "kv-cb-${substr(sha256(data.azurerm_subscription.current.id), 0, 10)}"
  image          = "ghcr.io/nevolevi/criteriabench@${var.image_digest}"
  job_runner_sha = filesha256("${path.module}/job_runner.py")
  job_payload    = base64gzip(file("${path.module}/job_runner.py"))
  job_bootstrap  = "import base64,gzip;exec(gzip.decompress(base64.b64decode('${local.job_payload}')))"
  common_tags = {
    project     = "criteriabench"
    environment = "production-proof"
    owner       = var.owner_tag
    managed-by  = "terraform"
    ephemeral   = "true"
    review-by   = var.review_at_utc
    cost-center = "personal-learning"
  }
}

resource "azurerm_resource_group" "this" {
  count = local.enabled ? 1 : 0

  name     = var.resource_group_name
  location = var.location
  tags     = local.common_tags
}

resource "azurerm_log_analytics_workspace" "this" {
  count = local.enabled ? 1 : 0

  name                = "log-criteriabench-prod-demo"
  location            = azurerm_resource_group.this[0].location
  resource_group_name = azurerm_resource_group.this[0].name
  sku                 = "PerGB2018"
  retention_in_days   = 30
  daily_quota_gb      = 0.5
  tags                = local.common_tags
}

resource "azurerm_container_app_environment" "this" {
  count = local.enabled ? 1 : 0

  name                       = "cae-criteriabench-prod-demo"
  location                   = azurerm_resource_group.this[0].location
  resource_group_name        = azurerm_resource_group.this[0].name
  log_analytics_workspace_id = azurerm_log_analytics_workspace.this[0].id
  public_network_access      = "Enabled"
  tags                       = local.common_tags
}

resource "azurerm_user_assigned_identity" "job" {
  count = local.enabled ? 1 : 0

  name                = "id-criteriabench-live-job"
  location            = azurerm_resource_group.this[0].location
  resource_group_name = azurerm_resource_group.this[0].name
  tags                = local.common_tags
}

resource "azurerm_key_vault" "this" {
  count = local.enabled ? 1 : 0

  name                          = local.key_vault_name
  location                      = azurerm_resource_group.this[0].location
  resource_group_name           = azurerm_resource_group.this[0].name
  tenant_id                     = data.azurerm_client_config.current.tenant_id
  sku_name                      = "standard"
  rbac_authorization_enabled    = true
  public_network_access_enabled = true
  purge_protection_enabled      = false
  soft_delete_retention_days    = 7
  tags                          = local.common_tags
}

resource "azurerm_role_assignment" "job_secret_reader" {
  count = local.enabled ? 1 : 0

  scope                = azurerm_key_vault.this[0].id
  role_definition_name = "Key Vault Secrets User"
  principal_id         = azurerm_user_assigned_identity.job[0].principal_id
}

resource "azurerm_role_assignment" "operator_secret_writer" {
  count = local.enabled ? 1 : 0

  scope                = azurerm_key_vault.this[0].id
  role_definition_name = "Key Vault Secrets Officer"
  principal_id         = data.azurerm_client_config.current.object_id
}

resource "azurerm_consumption_budget_subscription" "this" {
  count = local.enabled ? 1 : 0

  name            = "criteriabench-prod-demo-budget"
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
      values   = [azurerm_resource_group.this[0].name]
    }
  }

  notification {
    enabled        = true
    threshold      = 50
    operator       = "GreaterThanOrEqualTo"
    threshold_type = "Actual"
    contact_emails = [var.budget_contact_email]
  }

  notification {
    enabled        = true
    threshold      = 100
    operator       = "GreaterThanOrEqualTo"
    threshold_type = "Forecasted"
    contact_emails = [var.budget_contact_email]
  }
}

resource "azurerm_container_app_job" "live" {
  count = local.job_enabled ? 1 : 0

  name                         = "criteriabench-live-job"
  location                     = azurerm_resource_group.this[0].location
  resource_group_name          = azurerm_resource_group.this[0].name
  container_app_environment_id = azurerm_container_app_environment.this[0].id
  replica_timeout_in_seconds   = 300
  replica_retry_limit          = 0
  workload_profile_name        = "Consumption"
  tags                         = local.common_tags

  manual_trigger_config {
    parallelism              = 1
    replica_completion_count = 1
  }

  identity {
    type         = "UserAssigned"
    identity_ids = [azurerm_user_assigned_identity.job[0].id]
  }

  secret {
    name                = "openai-key"
    identity            = azurerm_user_assigned_identity.job[0].id
    key_vault_secret_id = "${azurerm_key_vault.this[0].vault_uri}secrets/openai-api-key"
  }

  template {
    container {
      name    = "criteriabench-live"
      image   = local.image
      cpu     = 0.25
      memory  = "0.5Gi"
      command = ["python", "-c"]
      args    = [local.job_bootstrap]

      env {
        name        = "OPENAI_API_KEY"
        secret_name = "openai-key"
      }
      env {
        name  = "LLM_PROVIDER"
        value = "openai"
      }
      env {
        name  = "ALLOW_PAID_CALLS"
        value = "true"
      }
      env {
        name  = "LIVE_RUN_BUDGET_USD"
        value = "0.02"
      }
      env {
        name  = "OPENAI_MODEL"
        value = "gpt-5.6-luna"
      }
      env {
        name  = "PRICING_MODEL"
        value = "gpt-5.6-luna"
      }
      env {
        name  = "INPUT_COST_PER_MILLION_USD"
        value = "0.20"
      }
      env {
        name  = "OUTPUT_COST_PER_MILLION_USD"
        value = "1.20"
      }
      env {
        name  = "CRITERIABENCH_OPENAI_MAX_RETRIES"
        value = "0"
      }
      env {
        name  = "CRITERIABENCH_ENVIRONMENT"
        value = "production"
      }
      env {
        name  = "CRITERIABENCH_IMAGE_DIGEST"
        value = var.image_digest
      }
      env {
        name  = "CRITERIABENCH_JOB_RUNNER_SHA256"
        value = local.job_runner_sha
      }
    }
  }

  depends_on = [azurerm_role_assignment.job_secret_reader]
}
