# Security, privacy, cost, and evidence boundaries

This document describes the implemented CriteriaBench demonstration and clearly separates it from future production work.

Last factual review: **2026-09-01**.

> CriteriaBench is not a medical device and must not make patient-care or trial-enrolment decisions.

## Current trust boundary

CriteriaBench handles public trial text and developer-written synthetic references. It is not designed for patient records, protected health information, credentials, proprietary corpora, or other sensitive data.

The HTTP API has no authentication, tenant isolation, public rate limiting, global raw-request-body byte limit, or production ingress policy. Pydantic applies typed field, text-length, and batch-size limits only after the framework accepts and parses the request body. Keep it on loopback or a trusted private network and in mock mode. Do not publicly expose the API, PostgreSQL, Redis, Prometheus, Grafana, or worker metrics.

| Surface | Allowed now | Not allowed or claimed |
|---|---|---|
| API | Mock sync/async extraction, evaluation, benchmarks, run retrieval | Paid providers, public production service, sensitive input |
| Worker | Frozen mock jobs from Redis | OpenAI, downloads, multiple workers, exactly-once delivery |
| Benchmark CLI | Offline mock; explicitly authorized live benchmark | Unattended paid service, arbitrary model/rates/host, unlimited spending |
| Downloader | One or more validated NCT IDs, fetched one at a time from the fixed ClinicalTrials.gov HTTPS host | Arbitrary URLs, complete upstream provenance, worker fetches |
| Local orchestration | Loopback Compose and kind demonstrations | Production database, backup, identity, or secret management |
| Azure | Destroyed ephemeral mock-only AKS proof; one currently deployed but idle, no-ingress manual Container Apps benchmark Job with managed identity and a Key Vault secret reference, review-by 2026-09-15 | Public or continuously serving service, hard cost cap, production security posture, sensitive input |

## Secrets and dotenv files

Implemented safeguards:

- application settings do not automatically load repository dotenv files;
- the OpenAI key uses a secret-aware settings type and is never returned by `/info`;
- mock mode and `ALLOW_PAID_CALLS=false` are the defaults;
- CI and the provided Compose, Kubernetes, Helm, and AKS API/worker paths do not inject a key; a source-launched API or worker may inherit an ambient key through `Settings`, but its runtime gate prohibits paid calls;
- the manual Container Apps benchmark Job is an isolated paid path: it resolves the key through a user-assigned managed identity and Key Vault secret reference, and no key literal is stored in Terraform or the job definition;
- the live adapter pins `https://api.openai.com/v1` instead of honoring an environment-supplied alternate base URL;
- the adapter requests no provider-side response storage;
- `.gitignore` and `.dockerignore` exclude dotenv files, artifacts, local databases, Terraform state, kubeconfigs, and tool/test outputs; and
- canonical synthetic and manifested-live benchmark outputs omit the application key and redact absolute machine paths; arbitrary offline input can appear in an artifact and requires operator review before sharing.

Docker Compose normally discovers `.env` implicitly. Routine commands must use `scripts/compose-safe.ps1`, which clears dotenv-related Compose variables, rejects env-file and alternate-file arguments, and invokes only the repository's absolute canonical `compose.yaml`.

Operator rules:

- Never commit or paste a key, dotenv file, cloud credential, kubeconfig, Terraform state/plan, private fixture, Authorization header, or full process environment.
- Never pass a key as a Docker build argument or literal command-line argument; image metadata, process listings, and shell history can retain it.
- Supply a key only to the deliberately started local live benchmark process or the guarded in-memory Key Vault import path; remove or revoke it when the corresponding proof environment is retired.
- If exposure is possible, revoke/rotate first. Deleting text or history does not invalidate a credential.

Kubernetes Secrets are not a complete secret-management system. See Kubernetes' [Secrets good practices](https://kubernetes.io/docs/concepts/security/secrets-good-practices/).

## Paid-model isolation

The supported paid surfaces are the guarded local benchmark CLI and the separately authorized manual Container Apps benchmark Job. The API and worker remain mock-only. A local live run requires, simultaneously:

- `LLM_PROVIDER=openai`;
- `ALLOW_PAID_CALLS=true`;
- a non-empty process `OPENAI_API_KEY`;
- reviewed `gpt-5.6-luna` with the exact configured input/output rates;
- `--live` and `--acknowledge-paid-api`;
- an explicit positive `--budget-usd` no greater than USD 2;
- manifested input whose verified bytes are the bytes parsed; and
- an output beneath `artifacts/`, with overwrite refusal by default.

The CLI reserves a retry-aware worst-case estimate for the entire batch before the first request, runs sequentially, conservatively consumes an authorization reservation once a provider call starts, stops after an error, writes a unique temporary artifact, and atomically replaces the requested output. It does not claim filesystem `fsync` durability. Extraction and evaluation hashes bind relevant implementation code and schema. The Container Apps path further fixes one manual execution, zero job retries, a 300-second timeout, and a USD 0.02 application authorization guard.

These controls cannot guarantee a bill. Failed, timed-out, or retried requests can be charged; token estimates can differ from provider accounting; pricing can change; and other use of the same key is outside this repository. The USD 2 CLI maximum and USD 0.02 Container Apps guard are application authorization ceilings, not OpenAI account caps. The artifact's usage-priced cost is an estimate under configured rates, not a provider invoice. Verify the current model and rates on the official [Luna model page](https://developers.openai.com/api/docs/models/gpt-5.6-luna) immediately before a run and reconcile the artifact with the provider dashboard.

No paid run is necessary to demonstrate the architecture. The live history is deliberately narrow: two local attempts failed closed on provenance before a scored result, then one approved local one-case smoke succeeded; two Container Apps attempts failed before job start with zero executions and were fully cleaned up, then one approved execution succeeded. The successful results are synthetic one-case engineering smokes, not clinical validation or statistically meaningful model-quality evidence. No zero-provider-billing claim is made for the failed local attempts.

## Input and data privacy

Trial text submitted with persistence enabled is stored with the run. Use only public or synthetic text. The development database has no formal retention/deletion policy.

The ClinicalTrials.gov client:

- accepts only an NCT identifier;
- calls the fixed official HTTPS host and rejects redirects;
- streams into a bounded response buffer;
- validates the returned NCT identifier; and
- retains only NCT ID, brief title, eligibility text, and source URL.

The complete upstream single-study response exists transiently during mapping, so it is inaccurate to claim that unrelated modules are never fetched. They are not retained or sent to the extractor. Current fixtures do not preserve the upstream API version, data timestamp, last update, or raw-response hash.

Canonical synthetic and manifested-live artifacts use public/synthetic input, repository-relative paths, fixture hashes, provider/model, contract hashes, maximum permitted attempts per case, configured price assumptions, usage-priced cost, extraction, and evaluation. Arbitrary caller-provided offline input can appear in an artifact, so operators must inspect every output before sharing. Never publish a subscription ID, tenant, email/domain, machine path, credential, or private input.

## Evaluation validity

Evaluation is an engineering smoke metric, not clinical validation.

- Exact precision/recall/F1 uses normalized criterion text plus criterion kind and counts duplicates correctly.
- Token F1 uses deterministic maximum-weight one-to-one Hungarian assignment among same-kind criteria, after a 0.25 similarity floor.
- Field accuracy covers category, concept, operator, value, unit, negation, temporal relation, and logic connector.
- It does not yet score temporal quantity/unit/reference, evidence similarity, logic-parent topology, or ambiguities.
- Extractor/provider prediction evidence offsets and quotes are separately validated against the exact input text; manually authored gold/reference evidence is typed but is not currently source-cross-checked.
- `schema_valid=true` means typed validation succeeded, not that a result is true, complete, medically safe, or source-equivalent.
- One synthetic reference is only a reproducibility smoke.

A research claim needs a larger locked corpus, independent annotation, adjudication, fixed sampling/model settings, uncertainty estimates, and error analysis.

## Queue and persistence semantics

The worker uses Redis atomic claim into a processing list, explicit acknowledgement, startup recovery, frozen provider/model/schema/code contracts, exact queued-trial versus stored-request validation, and an atomic dead-letter move capped at 100 entries.

PostgreSQL and Redis are not one distributed transaction. A crash can still cause duplicate execution or a temporary processing-list item. The single worker recovers processing items on restart; there is no general delayed retry/backoff engine. Paid jobs are prohibited, which removes paid replay risk. The correct claim is **at least once with fail-closed validation and best-effort idempotency**, not exactly once.

## Observability

Prometheus counters/histograms and API/worker metrics endpoints are implemented. Labels are bounded and must never include raw URLs, trial IDs, source text, exception messages, or job UUIDs.

OpenTelemetry application tracing is future work. The presence of collector configuration is not evidence that spans are emitted, scrubbed, received, or stored. Before enabling automatic instrumentation, explicitly exclude credentials, headers, trial text, prompts, and model output.

## Containers and local Kubernetes

The production image is multi-stage, non-root, read-only at runtime where orchestrated, and built from `uv.lock` using digest-pinned Python and uv images. CI uses frozen dependency resolution. GitHub Action pins and dependency updates still require periodic review.

Normal Compose publishes only the API on `127.0.0.1`. The separate local integration evidence used loopback-only PostgreSQL/Redis ports. kind exposes only `127.0.0.1:8080`; repeat runs recreate the migration Job and restart API/worker pods so the local image tag cannot mask stale code.

The built-in PostgreSQL/Redis in kind/Helm are ephemeral demonstrations. They are not durable, backed up, highly available, or suitable for public use. Helm's generated demo credential is namespace-local and disposable, not a production secret design. Network policies are rendered; enforcement depends on the cluster CNI (the planned AKS configuration uses Cilium, while a default kind cluster is not production evidence).

## Azure cost and identity

The AKS Terraform definition is intentionally small: a parent resource group, AKS free-tier control plane, one bounded worker node, Azure CNI Overlay with Cilium, Microsoft Entra/Azure RBAC configuration, an explicit managed node resource group, TTL metadata, and combined budget alerts.

An explicitly approved ephemeral mock-only AKS proof was applied on 2026-09-01 with immutable image `sha256:a23de765a424d74d205f84e4255d572ab5cc79bd7774af034cfa9dca804d8ba2`. AKS health and readiness were up; sync extraction returned 200; async extraction returned 202 and the worker completed; the result contained one inclusion and one exclusion criterion under schema 1.0 with zero tokens and USD 0 cost; and API and worker metrics were observed.

Teardown was independently confirmed: the AKS parent and managed-node resource groups and budget were absent, Terraform retained only data-source entries and no managed resources, and temporary proof artifacts were absent. That AKS proof was not a production deployment.

The separate Container Apps definition creates one no-ingress, manually triggered benchmark Job pinned to image `sha256:94bb5ca7ebf26a331a202cacd455ce922db954f71697229df5439775f9a5b9ad`, with `retries=0`, timeout 300 seconds, 0.25 CPU, 0.5 GiB memory, a user-assigned managed identity, and a Key Vault-backed secret reference with no literal key. Its EUR 15 budget alert is a delayed notification, not a hard cap or automatic deletion. The job is currently deployed but idle and tagged for review by 2026-09-15.

The supported AKS workflow is:

1. `scripts/azure-preflight.ps1` validates identity, subscription, tools, safety inputs, SKU availability, and sufficient live VM-family and total regional vCPU quota; the result is a point-in-time snapshot, not a capacity reservation or spending cap.
2. `scripts/azure-plan.ps1` creates an expiring plan plus summary/hash.
3. A human reviews exact resources and current Azure Retail Prices.
4. `scripts/azure-apply-reviewed.ps1` accepts only the matching plan hash and image digest, waits for migrations/workloads, and attempts automatic cleanup after failure.
5. `scripts/azure-destroy.ps1` destroys and verifies both the parent and AKS-managed node resource groups.

The Container Apps workflow uses `scripts/container-apps-safety.ps1`, `scripts/container-apps-deploy.ps1`, and `scripts/container-apps-destroy.ps1`; failure before successful completion triggers cleanup of billable resources, while ordinary cleanup does not purge a soft-deleted Key Vault.

The budget value is in the subscription's billing currency. Azure budget alerts are delayed notifications, not hard caps and not automatic deletion. Pay-as-you-go subscriptions generally do not have the credit-based spending-limit switch described for eligible subscriptions. See Microsoft's [spending-limit behavior](https://learn.microsoft.com/en-us/azure/cost-management-billing/manage/spending-limit) and [budget documentation](https://learn.microsoft.com/en-us/azure/cost-management-billing/costs/tutorial-acm-create-budgets).

Current execution uses a local Azure CLI/Terraform operator identity and local Terraform state. The Container Apps path implements a user-assigned managed identity and Key Vault secret reference; GitHub-to-Azure OIDC, remote locked state, end-to-end AKS workload identity, and a full production API identity design remain future work.

## Evidence ledger

| Claim | Evidence on 2026-09-01 |
|---|---|
| Static/unit behavior | Ruff, formatting, strict mypy, compile, and non-live suite pass above the 75% coverage gate |
| Dependency/container reproducibility | `uv.lock` check/frozen resolution and digest-pinned production image build/import pass |
| PostgreSQL/Redis | Real migration downgrade/upgrade, repository round-trip, queue recovery, ACK, and dead-letter tests pass |
| Compose | Health/readiness, sync/async extraction, persistence, worker, and metrics pass in mock mode |
| Kustomize/kind | Clean loopback cluster, non-root PostgreSQL, migration, API/worker, sync/async smoke, repeat-safe script, and teardown pass |
| Helm | Lint/render plus separate runtime install, migration, sync/async smoke, and uninstall pass |
| Terraform | Offline format/init/validate pass; the AKS plan was applied and destroyed; the Container Apps plan was applied and the manual job remains idle |
| Local live model | Two provenance failures followed by one successful guarded one-case Luna smoke: 1,083 input/295 output tokens, usage-priced estimate USD 0.000571, USD 0.0111 authorization consumed under USD 0.02, exact F1 0.5, token F1 0.75, macro field accuracy 1.0; no zero-billing claim for failures |
| Container Apps live model | Two pre-start attempts produced zero executions and fully cleaned; the next produced exactly one `Succeeded` execution and one Luna attempt: 1,083 input/296 output tokens, usage-priced estimate USD 0.000572, USD 0.0111 authorization consumed under USD 0.02, latency 5,764.961 ms, valid schema, prediction/reference 2/2 with one inclusion and one exclusion each, exact F1 0.0, token F1 0.5, macro field accuracy 1.0 |
| Azure | Destroyed mock-only AKS proof plus one currently deployed but idle no-ingress manual Container Apps Job, review-by 2026-09-15; not a public service or production-readiness claim |
| Identity and secrets | Key Vault secret reference and user-assigned managed identity implemented for the Container Apps job; OTel, OIDC, remote state, end-to-end AKS workload identity, and full production API identity remain future work |

Infrastructure files demonstrate engineering intent; successful execution of the same committed revision is evidence. CI should be treated as the release source of truth after publication. Both paid successes are one-case synthetic engineering smokes, not clinical or statistically meaningful model-quality evidence.

## Release checklist

1. Confirm tracked files contain no dotenv, key, cloud ID, state/plan, kubeconfig, private fixture, machine path, or sensitive artifact.
2. Check `uv.lock`, run static/non-live tests, and run real PostgreSQL/Redis integration tests.
3. Run the zero-cost gold benchmark and validate its expected scores/hashes/cost.
4. Build and scan the locked image; smoke Compose.
5. Lint/render Helm and Kustomize; run local cluster evidence when making deployment claims.
6. Confirm API/worker remain mock-only and the official live endpoint/model/rates remain pinned.
7. Describe the evidence trail exactly: two local provenance failures then one local success; two Container Apps pre-start failures with zero executions and full cleanup, then one successful execution. Keep usage-priced cost, application authorization, provider billing, and delayed Azure budget alerts distinct, and describe all paid results only as one-case synthetic engineering smokes.
