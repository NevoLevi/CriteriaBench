provider "azurerm" {
  features {
    key_vault {
      purge_soft_delete_on_destroy    = false
      recover_soft_deleted_key_vaults = true
    }
  }

  resource_provider_registrations = "none"
}

data "azurerm_client_config" "current" {}
data "azurerm_subscription" "current" {}
