# Operations runbook

This runbook separates reproducible offline work from the one-shot paid Luna canary. The Real-v1 canary has not run, and no locked-test run is authorized.

The reviewed PowerShell wrappers below are the supported operator interface. They fail closed on existing outputs and mismatched paths, hashes, image IDs, expiry, or artifact lineage. At the time of this document, no Real-v1 plan, execution binding, paid authorization, provider attempt, or charge exists.

## Prerequisites

- Windows PowerShell;
- Docker Desktop using Linux containers;
- Git;
- the repository-local `uv` at `.tools\uv\uv.exe`; and
- only for the paid phase, a direct OpenAI API key with API billing enabled.

Azure CLI and `az login` are **not required** for the direct Real-v1 Luna run. An Entra error such as `530035` for the Microsoft Azure CLI does not block this benchmark.

Never paste an API key into chat, a command, an environment variable, Docker configuration, or a repository file. The live wrapper will ask for it in a hidden prompt at the final execution phase.

## 1. Verify source and offline evidence

From the repository root:

```powershell
.\.tools\uv\uv.exe lock --check
.\.tools\uv\uv.exe sync --frozen --extra dev
.\.tools\uv\uv.exe run --frozen --no-env-file ruff check .
.\.tools\uv\uv.exe run --frozen --no-env-file ruff format --check .
.\.tools\uv\uv.exe run --frozen --no-env-file mypy src
.\.tools\uv\uv.exe run --frozen --no-env-file pytest -m "not live and not integration"
```

The focused Real-v1 verification is:

```powershell
.\.tools\uv\uv.exe run --frozen --no-env-file pytest `
  tests/test_llf_import.py `
  tests/test_llf_semantics.py `
  tests/test_llf_semantic_evaluation.py `
  tests/test_llf_baselines.py `
  tests/test_llf_agreement.py `
  tests/test_llf_canary_preregistration.py `
  tests/test_llf_live_score.py `
  tests/test_real_live.py
```

These commands must use no provider, network, environment secret, or locked-test model output.

Before any paid plan, reproduce and inspect:

- 2,000 source-only generation cases and 885 trial assignments;
- exactly 200 development and 1,800 test cases, with 86/799 trials and no overlap;
- 1,997/1,997 available primary references parsed and three disclosed missing upstream rows;
- byte-stable full/development/test coverage artifacts;
- the 20 × 3 human-agreement report, including three malformed annotations and six unavailable pairs;
- the deterministic all-development BM25 evidence; and
- the exact 25-case canary selection/BM25 preregistration.

Any unexpected diff requires investigation and a new version where appropriate. Do not “refresh” a frozen artifact in place to make a check pass.

## 2. Build and identify one exact image

Build the locked production image only after all code, prompt, schema, evaluator, preregistration builder, and dependency changes are complete. Scan/test those exact bytes. Resolve the local image to `sha256:<64 hex>` and retain that image ID.

All subsequent offline and live phases must use the exact image ID, not a mutable tag. A plan for one image cannot authorize another.

The image must contain `uv.lock` and the complete frozen Python execution inventory. The paid container must run read-only, non-root, with all capabilities dropped, `no-new-privileges`, a PID limit, and only the declared mounts.

Set the paths and the intended one-shot identities once. Replace the three angle-bracket values in this setup block; do not reuse a run or authorization ID from another execution:

```powershell
$repoRoot = (Resolve-Path -LiteralPath '.').Path
$generationRoot = Join-Path $repoRoot 'data\real\llf'
$coverageRoot = Join-Path $repoRoot 'docs\results'
$artifactRoot = Join-Path $repoRoot 'artifacts\real-v1-luna'
$reportRoot = Join-Path $artifactRoot 'reports'
$publicPreregistration = Join-Path $coverageRoot 'llf-canary-preregistration.json'
$image = '<exact-reviewed-local-tag-or-digest>'
$runId = '<new-run-id>'
$authorizationId = '<new-authorization-id>'

New-Item -ItemType Directory -Path $artifactRoot -ErrorAction Stop | Out-Null
New-Item -ItemType Directory -Path $reportRoot -ErrorAction Stop | Out-Null
```

`$image` may be a tag for discovery, but every wrapper resolves it to one local `sha256:<64 hex>` image ID and binds that ID. Keep the resolved image bytes available for every later phase.

## 3. Create and publish the static canary preregistration

Build the canonical artifact once, then independently reproduce it:

```powershell
if (Test-Path -LiteralPath $publicPreregistration) {
  throw 'Refusing to replace an existing public preregistration'
}

.\.tools\uv\uv.exe run --frozen --no-env-file `
  criteriabench-llf-canary-preregister build `
  --dataset-dir $generationRoot `
  --coverage-dir $coverageRoot `
  --output $publicPreregistration

.\.tools\uv\uv.exe run --frozen --no-env-file `
  criteriabench-llf-canary-preregister check `
  --dataset-dir $generationRoot `
  --coverage-dir $coverageRoot `
  --artifact $publicPreregistration
```

The provider-free builder uses:

- source-only development generation artifacts;
- development references and development coverage;
- the exact current code and `uv.lock`; and
- a builder with no provider, network, environment, or secret entry point.

The artifact must bind:

- the exact 25 cases from 25 different development trials;
- prompt-example trial exclusion and source-only selection strata;
- BM25 identity, training/prediction hashes, and frozen comparator scores;
- provider wire schema and local LLF parser;
- evaluator/runner/dependency identity;
- Luna configuration and price snapshot;
- `USD 0.163840000` reserved under the exact `USD 0.170000000` cap; and
- the complete conjunctive advancement gates.

Verify that its evidence scope says model/provider called `false`, network used `false`, secret/environment read `false`, locked references opened `false`, and locked-test evidence `false`.

Commit and push the exact preregistration bytes before proceeding to the exact plan. After that public checkpoint, copy—not regenerate—the bytes into the sealed artifact root used by the wrappers and verify byte equality:

```powershell
$preregistrationPath = Join-Path $artifactRoot 'llf-canary-preregistration.json'
if (Test-Path -LiteralPath $preregistrationPath) {
  throw 'Refusing to replace the sealed preregistration copy'
}
Copy-Item -LiteralPath $publicPreregistration -Destination $preregistrationPath -ErrorAction Stop
if ((Get-FileHash -Algorithm SHA256 $publicPreregistration).Hash -cne `
    (Get-FileHash -Algorithm SHA256 $preregistrationPath).Hash) {
  throw 'Public and sealed preregistration bytes differ'
}
```

Record both its canonical `preregistration_sha256` and file-byte SHA-256.

## 4. Create the exact offline plan

Create the plan in the exact image with networking disabled:

```powershell
$planFileName = 'llf-canary-plan.json'
$planPath = Join-Path $artifactRoot $planFileName
$planCreatedAtUtc = [DateTime]::UtcNow.ToString('yyyy-MM-ddTHH:mm:ssZ')

.\scripts\plan-real-luna-canary.ps1 `
  -ArtifactRoot $artifactRoot `
  -GenerationRoot $generationRoot `
  -Image $image `
  -CreatedAtUtc $planCreatedAtUtc `
  -PlanFileName $planFileName
```

The plan wrapper:

- runs the exact image with `--network=none`;
- mount only the source-only generation files plus a writable artifact root;
- select exactly the preregistered 25 cases in the same order;
- record a four-hour-or-shorter lifetime inside the frozen pricing window;
- bind the exact image, implementation, prompt/output/parser, model settings, rates, reservations, and cap; and
- create a plan only—never an authorization or provider call.

Review at minimum:

- plan and artifact-byte SHA-256;
- selected case-set SHA-256;
- preregistration match;
- exact image ID;
- purpose `development_llf_canary_25`;
- 25 cases / 25 trials;
- model `gpt-5.6-luna` and direct LLF output track;
- one attempt, zero retries, 60-second deadline;
- `USD 0.170000000` cap; and
- creation, expiry, and rate validity times.

If the price snapshot has expired or the full conservative remaining duration does not fit, stop. Do not extend an artifact by editing JSON. After independent review, type the displayed values into new variables; do not derive “reviewed” values automatically in the authorization command:

```powershell
$reviewedPreregistrationSha256 = '<reviewed-preregistration-sha256>'
$reviewedPlanSha256 = '<reviewed-plan-sha256>'
$reviewedCaseSetSha256 = '<reviewed-selected-case-set-sha256>'
```

## 5. Publish the one-execution binding

Create the one-shot binding offline:

```powershell
$executionBindingFileName = "$runId-execution-binding.json"
$executionBindingPath = Join-Path $artifactRoot $executionBindingFileName

.\scripts\bind-real-luna-canary.ps1 `
  -ArtifactRoot $artifactRoot `
  -Image $image `
  -PreregistrationPath $preregistrationPath `
  -PlanPath $planPath `
  -RunId $runId `
  -AuthorizationId $authorizationId `
  -ReviewedPreregistrationSha256 $reviewedPreregistrationSha256 `
  -ReviewedPlanSha256 $reviewedPlanSha256 `
  -ExecutionBindingFileName $executionBindingFileName
```

The binding joins exactly one preregistration and plan to:

- one intended run ID;
- one intended authorization ID;
- `/run/artifacts/output` inside the container;
- the normalized exact host output directory hash;
- the separate durable authorization-state directory hash;
- the exact image and case/configuration/cost fields;
- maximum execution count one;
- optional stopping prohibited;
- the versioned-quality-failure policy; and
- the new-binding/fresh-authorization/full-disclosure operational-rerun policy.

Publish or commit byte-for-byte copies of the exact plan and execution binding before authorization. Confirm the public byte hashes equal `$planPath` and `$executionBindingPath`, and confirm the binding reproduces from the public preregistration and exact plan; do not merely compare a few displayed fields.

After that public review, type the displayed binding hash into a new variable:

```powershell
$reviewedExecutionBindingSha256 = '<reviewed-execution-binding-sha256>'
```

## 6. Obtain fresh exact authorization

Only after the user gives the exact acknowledgement for the reviewed public chain, create the fresh authorization offline:

```powershell
$authorizationFileName = 'llf-canary-authorization.json'
$authorizationPath = Join-Path $artifactRoot $authorizationFileName
$authorizedAtUtc = [DateTime]::UtcNow.ToString('yyyy-MM-ddTHH:mm:ssZ')

.\scripts\authorize-real-luna-canary.ps1 `
  -ArtifactRoot $artifactRoot `
  -Image $image `
  -PlanPath $planPath `
  -PreregistrationPath $preregistrationPath `
  -ExecutionBindingPath $executionBindingPath `
  -ReviewedPlanSha256 $reviewedPlanSha256 `
  -ReviewedPreregistrationSha256 $reviewedPreregistrationSha256 `
  -ReviewedExecutionBindingSha256 $reviewedExecutionBindingSha256 `
  -ReviewedCaseSetSha256 $reviewedCaseSetSha256 `
  -ApprovedBudgetCapUsd '0.170000000' `
  -CanaryAcknowledgement 'I authorize this exact sealed 25-case LLF semantic paid Luna canary plan.' `
  -AuthorizedAtUtc $authorizedAtUtc `
  -AuthorizationId $authorizationId `
  -RunId $runId `
  -AuthorizationFileName $authorizationFileName
```

Authorization runs with networking disabled and requires the exact:

- preregistration and artifact-byte hashes;
- plan and artifact-byte hashes;
- execution-binding and artifact-byte hashes;
- image ID, case-set hash, run ID, authorization ID, and path hashes;
- `USD 0.170000000` cap;
- authorization time and expiry; and
- acknowledgement text:

> I authorize this exact sealed 25-case LLF semantic paid Luna canary plan.

The resulting authorization is not reusable for another root, run, binding, plan, image, case set, price snapshot, or time window. It is also not authorization for the locked test.

The binding step creates the intended output directory and separate durable authorization-state directory. Authorization re-resolves and verifies those same path scopes. Neither step creates a provider attempt.

## 7. Recover/preflight offline, then execute once

Use the exact reviewed chain. Do not set or export `OPENAI_API_KEY`:

```powershell
$runDirectory = Join-Path $artifactRoot $runId
$authorizationStateDirectory = Join-Path $artifactRoot '.real-live-authorization-state'

.\scripts\run-real-luna.ps1 `
  -ArtifactRoot $artifactRoot `
  -GenerationRoot $generationRoot `
  -PlanPath $planPath `
  -AuthorizationPath $authorizationPath `
  -PreregistrationPath $preregistrationPath `
  -ExecutionBindingPath $executionBindingPath `
  -OutputDirectory $runDirectory `
  -RunId $runId `
  -Image $image
```

Before it requests a key, this wrapper always invokes the core `recover` command in the exact image with `--network=none`. Recovery validates/reconciles the sealed prefix and prints `recovery_remaining=N`. If `N=0`, the wrapper returns without requesting a key. If work safely remains, it continues to the hidden prompt and live command.

The wrapper displays:

```text
OpenAI API key (input hidden):
```

Paste/type the key there. Input must remain hidden. The wrapper sends it over standard input to one interactive container and then clears its temporary plaintext representation.

Immediately before the first provider call, the runner must:

1. verify the preregistration → plan → binding → authorization chain;
2. verify exact image, source-only mounts, host/container path hashes, run ID, case order, and freshness;
3. prove the full remaining worst-case duration fits every expiry;
4. confirm the external authorization claim and local consumption are both absent or both present and identical for recovery; and
5. create the exclusive one-time authorization claim.

For each ordinal, it creates an external append-only attempt claim before the local pending request and provider call. It then writes exactly one matching attempt and one terminal case outcome. No retry is allowed.

Do not interrupt a healthy run. If interruption occurs, rerun the **same wrapper command above** with the same exact roots and artifacts. Its offline recovery must turn an already-started pending ordinal into an explicit failed outcome and seal any fatal prefix before another call. Never delete claims or artifacts to “start clean.” A separate operational execution requires a new public binding, fresh authorization, and full disclosure.

Any failure means the canary cannot pass. Preserve the complete directory and external ledger.

## 8. Score with networking disabled

After a terminal sealed summary exists, score the run:

```powershell
$scoreReportFileName = 'llf-canary-score.json'
$scoreReportPath = Join-Path $reportRoot $scoreReportFileName

.\scripts\score-real-luna-canary.ps1 `
  -RunDirectory $runDirectory `
  -AuthorizationStateDirectory $authorizationStateDirectory `
  -PreregistrationPath $preregistrationPath `
  -ExecutionBindingPath $executionBindingPath `
  -GenerationRoot $generationRoot `
  -CoverageRoot $coverageRoot `
  -ReportOutputDirectory $reportRoot `
  -Image $image `
  -ReportFileName $scoreReportFileName
```

The score phase must:

- use the same exact image ID;
- run with `--network=none`;
- mount the run and durable state read-only;
- add only development references and development coverage;
- write to a report directory disjoint from the run directory; and
- refuse to overwrite an existing report.

The scorer must validate all preregistration/binding/authorization/state/attempt/outcome/summary lineage before computing exact, node, edge, typed-component, usage, latency, provider-provenance, and operational metrics.

Do not hand-edit a run or score report. A mismatch is evidence of a failed integrity check, not a formatting problem.

## 9. Seal the PASS/FAIL decision

Create and immediately reproduce the sealed decision:

```powershell
$decisionFileName = 'llf-canary-advancement-decision.json'
$decisionPath = Join-Path $artifactRoot $decisionFileName

.\scripts\decide-real-luna-canary.ps1 `
  -ArtifactRoot $artifactRoot `
  -Image $image `
  -PreregistrationPath $preregistrationPath `
  -ExecutionBindingPath $executionBindingPath `
  -PlanPath $planPath `
  -AuthorizationPath $authorizationPath `
  -ScoreReportPath $scoreReportPath `
  -DecisionFileName $decisionFileName
```

The wrapper runs both `decide` and `check-decision` inside the exact image with `--network=none`. It emits every observed/required comparison and sets PASS only if all checks are true.

PASS requires:

- 25 attempted and 25 completed;
- zero failed, unattempted, or fatal-abort cases;
- known usage, observed latency, unique response ID, and required returned provider provenance for all 25;
- one attempt per case and no retries;
- charged consumption at most USD 0.17;
- p95 latency at most 60 seconds;
- combined node-plus-edge F1 at least 0.50 and at least 0.10 above BM25; and
- at least two exact trees.

If any check fails, the decision is FAIL and no locked plan may be authorized or run.

## 10. Reconcile and publish

After scoring:

1. independently reconcile response IDs, usage, and billing in the OpenAI dashboard;
2. retain the provider-dashboard evidence privately according to account policy;
3. publish the sealed source/preregistration/plan/binding/run/report/decision lineage after secret/path review;
4. disclose every failed or interrupted attempt; and
5. state public-benchmark contamination, alias drift, small-canary, human-agreement, missing-reference, and nonclinical limitations beside the result.

Internal hashes are reproducible lineage, not provider attestation. Usage-priced cost is not an invoice.

## PASS-gated locked planning; paid locked execution disabled

Do not even create a locked plan unless the canary decision is sealed PASS. The only supported planning command requires the complete canary chain:

```powershell
$lockedPlanFileName = 'llf-locked-plan.json'
$lockedPlanPath = Join-Path $artifactRoot $lockedPlanFileName
$lockedPlanCreatedAtUtc = [DateTime]::UtcNow.ToString('yyyy-MM-ddTHH:mm:ssZ')

.\scripts\plan-real-luna-locked.ps1 `
  -ArtifactRoot $artifactRoot `
  -GenerationRoot $generationRoot `
  -Image $image `
  -CreatedAtUtc $lockedPlanCreatedAtUtc `
  -PreregistrationPath $preregistrationPath `
  -ExecutionBindingPath $executionBindingPath `
  -CanaryPlanPath $planPath `
  -CanaryAuthorizationPath $authorizationPath `
  -ScoreReportPath $scoreReportPath `
  -AdvancementDecisionPath $decisionPath `
  -LockedPlanFileName $lockedPlanFileName
```

Both the host wrapper and the exact network-disabled image verify the full PASS lineage. The command creates a plan only; it creates no locked authorization and makes no provider call.

Even after PASS, the current locked constants are insufficient as an execution plan. The 1,800-case conservative duration is 30 hours at 60 seconds per case, longer than the current four-hour plan and two-hour authorization windows. The current pricing snapshot also expires.

A future paid locked phase needs refreshed official pricing, mechanically sufficient validity windows, a reviewed plan/binding protocol, and separate explicit authorization under its own acknowledgement. It is not covered by the USD 0.17 canary or any earlier general budget approval. Paid locked execution remains structurally disabled until that bounded authorization-window protocol is implemented.

## Local mock application

The older service path is safe to run locally in mock mode through the wrapper that disables implicit dotenv discovery:

```powershell
.\scripts\compose-safe.ps1 up --build -d --wait
.\scripts\compose-safe.ps1 ps
```

Endpoints:

- OpenAPI: <http://127.0.0.1:8000/docs>
- liveness: <http://127.0.0.1:8000/healthz>
- readiness: <http://127.0.0.1:8000/readyz>
- metrics: <http://127.0.0.1:8000/metrics>

Stop while preserving the named PostgreSQL volume:

```powershell
.\scripts\compose-safe.ps1 down
```

Only use `down --volumes` when the development database is confirmed disposable.

The disposable kind/Kustomize demonstration remains:

```powershell
.\scripts\kind-up.ps1
.\scripts\kind-down.ps1 -Confirmation DELETE-KIND
```

API/worker, Compose, kind, Helm, CI, and the old AKS service path remain mock-only.

## Historical Azure operations

The 2026-09-01 AKS proof was explicitly approved, exercised, and destroyed. The earlier Container Apps Job was a one-case synthetic no-ingress proof. Do not rerun, mutate, or present either as current production without a new scoped request, current cloud-state check, fresh cost review, and explicit authorization.

Azure budget alerts are delayed notifications, not hard caps. Terraform plan hashes, image digests, and teardown verification remain good infrastructure practice but are unrelated to direct Real-v1 authentication.

## Troubleshooting

### Docker is unavailable

Run `docker version` and require both client and server sections. Start Docker Desktop and wait for the Linux engine. Do not fall back to an unsealed host run.

### Azure sign-in says access denied

Ignore it for Real v1. The direct Luna run uses OpenAI credentials, not Azure CLI or Microsoft Entra authorization.

### Price, plan, or authorization expired

Stop before a provider request. Review current official pricing and create a new versioned price snapshot, plan, public execution binding, and fresh authorization. Never edit timestamps.

### Plan, binding, path, image, or hash mismatch

Treat it as a safety failure. Resolve the exact intended roots/image/artifacts and rebuild the chain. Do not weaken validation or copy an authorization to another directory.

### External claim exists but output is missing

The authorization is consumed and the pair is inconsistent. Preserve the state and investigate. Deleting the claim to reuse authorization is prohibited.

### Pending/attempt state exists after interruption

Use only the reviewed no-key recovery path first. It must reconcile the prefix and seal interruption/fatal state without contacting the provider. Do not manually remove pending or per-ordinal files.

### Provider returns 401, 429, timeout, refusal, or invalid output

Preserve it as the single attempt's failure. Do not retry the same authorization. A 429 may indicate quota or billing state and still fails the 25/25 gate.

### A key may have been exposed

Revoke or rotate it immediately. Deleting logs, messages, or files is not revocation. Inspect the repository and artifacts without printing the value.

## Evidence to retain

- source revision, artifact hashes, split and parser coverage;
- BM25 identity/results and human-agreement report;
- static preregistration and byte hash;
- exact image ID and scan/test evidence;
- plan, public execution binding, and authorization;
- external authorization/per-ordinal claims and local consumption;
- every request hash, attempt, outcome, pending recovery record, and summary;
- network-disabled score and decision reports;
- provider-dashboard reconciliation; and
- code revision, CI result, date, operator limitations, and full failure disclosure.
