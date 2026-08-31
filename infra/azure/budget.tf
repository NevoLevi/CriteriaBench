# The authoritative budget is defined in combined-budget.tf. It is scoped at
# subscription level but filtered to the exact parent and AKS node resource
# group names, so one alert amount covers compute, disks, and networking too.
# A parent-resource-group-only budget is intentionally not created because most
# AKS charges land in the separate managed node resource group.
