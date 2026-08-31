provider "azurerm" {
  features {}

  # Provider registration is explicit in the guarded deployment script so the
  # provider does not register unrelated Azure services.
  resource_provider_registrations = "none"
}

data "azurerm_client_config" "current" {}
