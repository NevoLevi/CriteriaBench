# Human access uses Microsoft Entra ID and Azure RBAC. There are no Kubernetes
# local admin credentials when local_account_disabled is enabled on the cluster.
resource "azurerm_role_assignment" "current_operator_cluster_admin" {
  count = local.enabled ? 1 : 0

  scope                = azurerm_kubernetes_cluster.this[0].id
  role_definition_name = "Azure Kubernetes Service RBAC Cluster Admin"
  principal_id         = data.azurerm_client_config.current.object_id
}
