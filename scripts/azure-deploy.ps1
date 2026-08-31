throw @"
Direct Azure deployment is intentionally disabled.

Use the two-step, hash-bound workflow:
  1. scripts\azure-plan.ps1 to create and review a saved plan.
  2. scripts\azure-apply-reviewed.ps1 with both printed SHA-256 values, an immutable GHCR digest, explicit billable-plan approval, and automatic failure cleanup.

This guard prevents an unreviewed plan or mutable image tag from creating billable resources.
"@
