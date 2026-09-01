# Operations runbook

All routine paths are mock-only and cost USD 0. Paid inference and Azure creation require separate explicit approval.

## Prerequisites

- Windows PowerShell 5.1 or PowerShell 7;
- Docker Desktop using Linux containers;
- Python 3.12 and uv 0.12.8 for source checks;
- project-local Helm/kind/Terraform tooling installed by the supplied scripts as needed; and
- Git.

Never inspect, print, commit, or pass `.env.local` to Docker/Compose/Kubernetes. The application does not load it automatically.

## Source checks

```powershell
uv lock --check
uv sync --frozen --extra dev
uv run --frozen --no-env-file ruff check .
uv run --frozen --no-env-file ruff format --check .
uv run --frozen --no-env-file mypy src
uv run --frozen --no-env-file python -m compileall -q src
uv run --frozen --no-env-file pytest -m "not live and not integration" `
  --cov=criteriabench --cov-fail-under=75
```

Real PostgreSQL/Redis integration tests require `TEST_POSTGRES_URL` and `TEST_REDIS_URL`. CI supplies them through isolated service containers and runs `pytest -m integration` as a separate fail-closed step.

## Docker Compose

Use only the wrapper. Plain Compose can discover a root `.env`, and alternate Compose files can change environment/network boundaries.

```powershell
.\scripts\compose-safe.ps1 up --build -d --wait
.\scripts\compose-safe.ps1 ps
```

Check:

```powershell
Invoke-RestMethod http://127.0.0.1:8000/healthz
Invoke-RestMethod http://127.0.0.1:8000/readyz
Invoke-WebRequest http://127.0.0.1:8000/metrics -UseBasicParsing
```

OpenAPI is at <http://127.0.0.1:8000/docs>. `/readyz` should report both database and Redis as `up`.

Stop while preserving the PostgreSQL volume:

```powershell
.\scripts\compose-safe.ps1 down
```

Deleting volumes is destructive and should be done only when the data is disposable.

## Offline benchmark

Choose a new output name or pass `--overwrite` deliberately:

```powershell
uv run --frozen --no-env-file criteriabench-benchmark `
  data/synthetic/benchmark_case_001.json `
  --output artifacts/smoke.json
```

Expected canonical smoke: one evaluated mock case, exact F1 1.0, token F1 1.0, macro field accuracy 0.875, cost USD 0, and 64-character extraction/evaluation hashes.

## Deliberate live benchmark

Do not run this for ordinary development. The wrapper reads only `OPENAI_API_KEY` from the exact ignored `.env.local` file, and only after non-secret tool/path/budget/switch checks pass. It scopes the key to the child process and restores the prior environment.

```powershell
.\scripts\run-live-benchmark.ps1 `
  -FixturePath data\synthetic\benchmark_case_001.json `
  -OutputPath artifacts\live-reviewed.json `
  -BudgetUsd 0.25 `
  -Live `
  -AcknowledgePaidApi
```

This still requires separate approval before execution. The wrapper pins uv 0.12.8; the CLI pins the official OpenAI endpoint, reviewed Luna model/rates, manifested input, whole-batch budget preflight, and USD 2 maximum application authorization. The guard is not a provider spending cap.

After a live run, inspect the JSON and reconcile its usage with the provider dashboard before publishing anything. The canonical manifested-live path omits the application key and redacts absolute machine paths, but operator review is still required. Never publish `.env.local` or shell/environment dumps.

Two early approved guarded Luna calls failed closed with `ProvenanceError`. After deterministic provenance repair, one separately approved local Luna run completed on 2026-09-01 against the single synthetic case: 1,083 input tokens, 295 output tokens, exact F1 0.5, token F1 0.75, macro field accuracy 1.0, and a USD 0.000571 usage-priced estimate. This is one-case engineering evidence, not clinical or comparative model-quality evidence. The estimate is not a provider invoice; provider billing can lag or differ.

## Local Kubernetes with kind/Kustomize

Create or refresh the exact disposable cluster:

```powershell
.\scripts\kind-up.ps1
```

The script builds/loads the locked local image, deletes a previous migration Job, reapplies the kind overlay, restarts API/worker, and waits for PostgreSQL, Redis, migration, API, and worker. The API is loopback-only at <http://127.0.0.1:8080/docs>.

Useful inspection commands (the script stores its kubeconfig below `.tools/kind`):

```powershell
$env:KUBECONFIG = (Resolve-Path .tools\kind\criteriabench.kubeconfig).Path
kubectl -n criteriabench get pods
kubectl -n criteriabench get job migrate
kubectl -n criteriabench logs deployment/api --tail=100
kubectl -n criteriabench logs deployment/worker --tail=100
```

Destroy the disposable cluster and its unrecoverable in-cluster data:

```powershell
.\scripts\kind-down.ps1 -Confirmation DELETE-KIND
```

## Helm

Static validation:

```powershell
helm lint deploy/helm/criteriabench --strict
helm template criteriabench deploy/helm/criteriabench `
  --namespace criteriabench > $env:TEMP\criteriabench-helm.yaml
```

For a local runtime proof, use the already loaded image and a separate disposable namespace:

```powershell
helm upgrade --install criteriabench-helm deploy/helm/criteriabench `
  --namespace criteriabench-helm --create-namespace `
  --set image.repository=criteriabench `
  --set image.tag=local `
  --set image.pullPolicy=Never `
  --rollback-on-failure --wait --wait-for-jobs --timeout 5m
```

Port-forward locally, smoke the API, then uninstall and delete the namespace. Demo PostgreSQL/Redis and the generated credential are ephemeral.

## Queue failure semantics

- Claimed work moves to a processing list until acknowledgement.
- A worker restart recovers stranded processing items to pending.
- A malformed envelope is moved atomically to a dead-letter list capped at 100.
- A contract, stored-request, or queued-trial mismatch becomes a stable terminal failure without extraction.
- There is one supported worker; do not scale it without redesigning recovery/locking.
- There is no general delayed retry scheduler. `retry_pending` remains processing until worker restart recovery.

Real Redis integration tests exercise claim/ACK, simulated crash recovery, and poison dead-letter behavior.

## Infrastructure validation (no Azure resources)

```powershell
.\scripts\validate-infra.ps1
```

This safely validates canonical Compose, Helm, Kustomize, Terraform formatting/init/validation and prints that no Azure resources were created.

## Historical AKS guarded proof

Do not use direct `terraform apply`/`destroy`. The supported sequence binds identity, subscription, reviewed plan bytes, immutable image digest, expiry, and cleanup.

The guarded default is one `Standard_D2as_v4` worker node. Preflight fails closed before planning when SKU availability or either VM-family or total regional vCPU quota is missing, restricted, malformed, or insufficient. There is no automatic SKU or region fallback; retry only after capacity becomes available or a deliberate, reviewed configuration change.

1. Run preflight with the exact subscription and budget contacts:

   ```powershell
   .\scripts\azure-preflight.ps1 <reviewed parameters>
   ```

2. Create the saved plan and summary:

   ```powershell
   .\scripts\azure-plan.ps1 <reviewed parameters>
   ```

3. Review the exact plan, both resource-group names, one-node SKU, TTL, billing currency, Azure Retail Prices, plan SHA-256, and immutable GHCR digest.
4. Obtain explicit billing approval.
5. Apply only the reviewed inputs:

   ```powershell
   .\scripts\azure-apply-reviewed.ps1 <plan hash, image digest, approval parameters>
   ```

6. Collect only mock deployment evidence.
7. Destroy immediately using the exact confirmation required by `scripts/azure-destroy.ps1`.
8. Verify that no managed Terraform resource addresses remain (residual data-source-only state is permitted), and that both the parent `rg-criteriabench-*` and explicitly configured AKS node resource group `rg-criteriabench-aks-nodes-*` no longer exist in CLI and the Azure portal. Cost data can lag.

An explicitly approved ephemeral mock-only proof followed this sequence on 2026-09-01 with immutable image `sha256:a23de765a424d74d205f84e4255d572ab5cc79bd7774af034cfa9dca804d8ba2`. AKS health and readiness were up; sync extraction returned 200; async extraction returned 202 and the worker completed; the result contained one inclusion and one exclusion criterion under schema 1.0 with zero tokens and USD 0 cost; and API and worker metrics were observed.

Teardown was independently confirmed: the parent and managed-node resource groups and the budget were absent, Terraform retained only data-source entries and no managed resources, and temporary proof artifacts were absent. No AKS deployment currently exists, and the proof was not a production deployment.

AKS budget alerts cover both groups in subscription billing currency but are notifications, not caps. The AKS workflow uses local Azure CLI/Terraform identity and local state; it does not implement OIDC, Key Vault, remote state, or workload identity. The separate Container Apps proof below implements a narrow user-assigned-identity/Key Vault secret reference; GitHub-to-Azure OIDC, remote state, and a full production data plane remain future work.

## Azure Container Apps production proof

An explicitly approved bounded production-mode proof completed on 2026-09-01 in Germany West Central. Terraform deployed a manual, no-ingress Consumption Container Apps Job from immutable image `sha256:94bb5ca7ebf26a331a202cacd455ce922db954f71697229df5439775f9a5b9ad`, with a user-assigned identity, an identity-backed Key Vault secret reference and no literal secret value, 0.25 CPU, 0.5 GiB memory, a 300-second timeout, zero replica retries, parallelism one, and one required completion. Exactly one execution reached `Succeeded`.

The successful job ran the reviewed Luna model once against the single synthetic case. Sanitized evidence reports 1,083 input tokens, 296 output tokens, 5,764.961 ms latency, one inclusion and one exclusion criterion, two predictions and two references, `schema_valid=true`, exact F1 0.0, token F1 0.5, and macro field accuracy 1.0. The USD 0.000572 usage-priced estimate and USD 0.0111 application authorization consumed under the USD 0.02 guard are repository accounting values, not a provider invoice. Provider and Azure billing data can lag or differ.

The job remains deployed but idle. Never start or rerun it without fresh explicit paid authorization. Its review-by value, `2026-09-15T14:58:49Z`, is an operator-reminder tag, not automatic teardown. The exact EUR 15 Azure budget is a delayed alert, not a hard spending cap.

Two earlier Container Apps infrastructure attempts failed before job start, produced zero executions, and were fully cleaned up before the successful run. They are useful as a repair trail, not model-run evidence. This proof demonstrates a bounded deployment/execution path only; one synthetic case does not establish clinical validity, model quality, or a customer-facing production service.

## Troubleshooting

### Readiness fails

Check PostgreSQL/Redis health, migration completion, and application logs. Do not log credentials or dump full environments.

### kind PostgreSQL cannot initialize

The manifest must retain UID/GID/fsGroup 70, `fsGroupChangePolicy: OnRootMismatch`, and `PGDATA=/var/lib/postgresql/data/pgdata`. Do not “fix” it with a privileged root init container.

### A repeat kind run shows stale behavior

Use `scripts/kind-up.ps1`; it recreates the migration Job and restarts API/worker after loading the image. A manual `kubectl apply` alone can leave the same local image tag running.

### A queue item is stranded

Restart the single worker and confirm startup recovery. Inspect queue depth/dead-letter metrics without printing payload/source text.

### A secret might be exposed

Stop the affected run, revoke/rotate at the provider, inspect provider/audit logs, remove the value from future files/history, and follow GitHub history-removal guidance if committed. Rotation is the containment step.

## Evidence to retain

For the committed revision, retain CI links/artifacts for static/tests, real services, offline benchmark, infrastructure render/validate, exact image scan/digest, and approved deployment evidence. Retain the AKS proof only as a historical apply/destroy result. Retain sanitized evidence for the successful local Luna run and the currently deployed but idle Container Apps Job, and reconcile usage-priced estimates with provider/Azure billing when available. Treat the two local `ProvenanceError` calls and two cleaned pre-start Container Apps failures as repair history, not successful-run evidence. Never publish credentials, private cloud/account identifiers, or unreviewed raw logs.
