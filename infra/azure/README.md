# Disposable Azure demo

This Terraform stack is a deliberately small, short-lived AKS portfolio demo
in **Germany West Central**:

- one AKS Free-tier control plane;
- one allowlisted `Standard_D2as_v5` system node (two vCPU, 8 GB);
- Azure CNI Overlay with the Cilium data plane and enforced NetworkPolicy;
- Microsoft Entra/Azure RBAC authentication with local Kubernetes accounts disabled;
- explicit parent and AKS node resource-group names;
- one combined budget alert filtered to both exact resource groups, with actual
  thresholds at 50%, 80%, and 100% and a 100% forecast threshold;
- ownership and expiry tags.

It does **not** create ACR, Key Vault, Log Analytics, managed PostgreSQL/Redis,
ingress, or a private AKS API endpoint. The demo uses ephemeral in-cluster data
services and a public GHCR image selected by immutable digest. Access is through
`kubectl port-forward`; the AKS control-plane endpoint is public but requires
Entra authentication and Azure RBAC.

## Safety and cost model

`confirm_billable_deployment` defaults to `false`, so an ordinary Terraform
apply creates zero Azure resources. The supported flow is plan, human review,
and hash-bound apply. The apply script also requires automatic teardown after
any partial apply, Helm failure, or rollout failure.

The budget amount is in the subscription's **billing currency**. It is a delayed
notification boundary, not a hard cap and not permission to spend that amount.
Real controls are the single constrained node, eight-hour maximum TTL, exact
resource-group scopes, active supervision, and immediate teardown.

The Free tier removes the AKS control-plane charge, not node/disk/network usage.
A short four-to-eight-hour run should normally cost only a few currency units,
but regional retail price and quota must be checked again immediately before
creating a plan.

## Guarded lifecycle

Sign in interactively and run the read-only preflight:

```powershell
az login
.\scripts\azure-preflight.ps1 -Subscription "Azure subscription 1"
```

Create a saved plan. This does not apply it:

```powershell
.\scripts\azure-plan.ps1 `
  -Subscription "Azure subscription 1" `
  -BudgetEmail "your-address@example.com" `
  -BudgetAmount 15 `
  -TtlHours 8 `
  -ApproveBillablePlan
```

Review the complete Terraform plan, the plan summary, current Azure price/quota,
and both printed SHA-256 values. The saved plan, summary, state, and email-bearing
Terraform artifacts are ignored local files and must not be published.

Only after explicit approval, apply the exact reviewed artifacts and immutable
public GHCR digest:

```powershell
.\scripts\azure-apply-reviewed.ps1 `
  -Subscription "Azure subscription 1" `
  -ReviewedPlanSha256 "<64-hex-plan-hash>" `
  -ReviewedSummarySha256 "<64-hex-summary-hash>" `
  -ImageDigest "sha256:<64-hex-image-digest>" `
  -ApproveReviewedBillablePlan `
  -AutoDestroyOnFailure
```

The API and worker remain mock-only and receive no OpenAI key. Use the ignored
project kubeconfig printed by the script and access the API locally:

```powershell
kubectl -n criteriabench port-forward service/criteriabench-api 8000:80
```

Destroy immediately after evidence capture:

```powershell
.\scripts\azure-destroy.ps1 `
  -Subscription "Azure subscription 1" `
  -Confirmation "DESTROY-CRITERIABENCH"
```

The destroy script checks Terraform state and independently verifies that both
`rg-criteriabench-demo` and `rg-criteriabench-aks-nodes-demo` are absent. Recheck
Cost Management the next day because Azure usage reporting can lag.
