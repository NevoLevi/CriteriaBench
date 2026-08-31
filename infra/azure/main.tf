locals {
  enabled = var.confirm_billable_deployment
  common_tags = {
    project     = "criteriabench"
    environment = "portfolio-demo"
    owner       = var.owner_tag
    managed-by  = "terraform"
    ephemeral   = "true"
    expires-at  = var.expires_at_utc
    cost-center = "personal-learning"
  }
}

resource "azurerm_resource_group" "this" {
  count = local.enabled ? 1 : 0

  name     = var.resource_group_name
  location = var.location
  tags     = local.common_tags
}

resource "azurerm_kubernetes_cluster" "this" {
  count = local.enabled ? 1 : 0

  name                = var.cluster_name
  location            = azurerm_resource_group.this[0].location
  resource_group_name = azurerm_resource_group.this[0].name
  node_resource_group = var.node_resource_group_name
  dns_prefix          = "criteriabench-demo"

  sku_tier                          = "Free"
  role_based_access_control_enabled = true
  local_account_disabled            = true
  oidc_issuer_enabled               = true
  workload_identity_enabled         = true
  azure_policy_enabled              = false
  automatic_upgrade_channel         = "patch"
  node_os_upgrade_channel           = "NodeImage"

  azure_active_directory_role_based_access_control {
    azure_rbac_enabled = true
    tenant_id          = data.azurerm_client_config.current.tenant_id
  }

  default_node_pool {
    name                         = "system"
    vm_size                      = var.node_vm_size
    node_count                   = 1
    auto_scaling_enabled         = false
    max_pods                     = 30
    os_disk_size_gb              = 30
    os_disk_type                 = "Managed"
    only_critical_addons_enabled = false
    temporary_name_for_rotation  = "systemtmp"

    upgrade_settings {
      max_surge = "10%"
    }
  }

  identity {
    type = "SystemAssigned"
  }

  network_profile {
    network_plugin      = "azure"
    network_plugin_mode = "overlay"
    network_data_plane  = "cilium"
    network_policy      = "cilium"
    pod_cidr            = "10.244.0.0/16"
    service_cidr        = "10.0.0.0/16"
    dns_service_ip      = "10.0.0.10"
    load_balancer_sku   = "standard"
    outbound_type       = "loadBalancer"
  }

  tags = local.common_tags

  lifecycle {
    precondition {
      condition     = var.deployment_ttl_hours <= 8
      error_message = "AKS deployment TTL cannot exceed eight hours."
    }
  }
}
