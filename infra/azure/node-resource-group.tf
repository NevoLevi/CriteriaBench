variable "node_resource_group_name" {
  description = "Explicit AKS managed node resource group so budgets and teardown checks have a stable, project-scoped target."
  type        = string
  default     = "rg-criteriabench-aks-nodes-demo"

  validation {
    condition     = startswith(var.node_resource_group_name, "rg-criteriabench-aks-nodes-")
    error_message = "The AKS node resource group must start with rg-criteriabench-aks-nodes-."
  }
}
