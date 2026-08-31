output "resource_group_name" {
  description = "Terraform-owned CriteriaBench resource group, or null while the safety gate is closed."
  value       = try(azurerm_resource_group.this[0].name, null)
}

output "node_resource_group_name" {
  description = "AKS-managed group containing the VMSS, disks, load balancer, and public IP."
  value       = try(azurerm_kubernetes_cluster.this[0].node_resource_group, null)
}

output "cluster_name" {
  description = "AKS cluster name, or null while the safety gate is closed."
  value       = try(azurerm_kubernetes_cluster.this[0].name, null)
}

output "cluster_port_forward_command" {
  description = "Local-only access avoids a long-lived public application endpoint."
  value = local.enabled ? format(
    "kubectl -n criteriabench port-forward service/criteriabench-api 8000:80"
  ) : null
}

output "teardown_deadline_utc" {
  description = "Human-visible deadline. Azure does not automatically destroy resources based on this tag."
  value       = local.enabled ? var.expires_at_utc : null
}
