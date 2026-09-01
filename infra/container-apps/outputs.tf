output "resource_group_name" {
  value = try(azurerm_resource_group.this[0].name, null)
}

output "key_vault_name" {
  value = try(azurerm_key_vault.this[0].name, null)
}

output "key_vault_uri" {
  value = try(azurerm_key_vault.this[0].vault_uri, null)
}

output "job_name" {
  value = try(azurerm_container_app_job.live[0].name, null)
}

output "job_runner_sha256" {
  value = local.job_runner_sha
}

output "image_digest" {
  value = var.image_digest
}

output "review_at_utc" {
  value = var.review_at_utc
}
