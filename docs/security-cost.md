# Security, privacy, cost, and evidence boundaries

CriteriaBench is a public-data research benchmark plus a local mock service demonstration. It is not a medical device, clinical decision system, public production API, or safe processor for patient data.

The Real-v1 Luna canary is pending. No Real-v1 model result or charge exists yet.

## Current trust boundary

| Surface | Allowed | Prohibited or not claimed |
|---|---|---|
| Real-v1 offline tools | Import, bounded LLF parsing, BM25, agreement, preregistration, integrity checks, scoring | Provider calls, secret access, runtime locked-test use |
| Real-v1 paid runner | One exact, source-only, explicitly authorized Luna benchmark | Runtime gold, arbitrary models/endpoints, tools, retries, unattended service, GraphV2 |
| API and worker | Local/private mock extraction and evaluation | Paid provider, public exposure, sensitive input, exactly-once claim |
| CI | Frozen offline checks, image build/scan/publish, artifact reproduction | API keys, live model generation, paid benchmark |
| Local Compose/kind/Helm | Disposable mock demonstration on loopback/private cluster | Production database, backups, tenant isolation, public ingress |
| Historical Azure proofs | Dated mock AKS and one-case no-ingress Container Apps engineering evidence | Real-v1 authorization, current cloud-state guarantee, quality or production claim |

## Public data only

Real v1 uses public LCT/LLF clinical-trial eligibility text and research annotations. Do not provide patient records, protected health information, credentials, proprietary corpora, or unpublished clinical data.

Public availability does not eliminate attribution duties or training-contamination risk. The complete corpus/license boundary is in [Data use and provenance](data-use.md).

## Secret handling

The Real-v1 key path is narrow:

1. the operator starts the reviewed PowerShell run wrapper;
2. `Read-Host -AsSecureString` displays a hidden prompt;
3. PowerShell unwraps the value only long enough to stream it over standard input to one interactive container process; and
4. the in-memory strings are cleared/released after the process exits.

The wrapper and CLI prohibit combining standard-input mode with an `OPENAI_API_KEY` environment variable. The exact key must never appear in:

- chat or an issue;
- `.env` or `.env.local`;
- source, test fixture, JSON, YAML, Terraform, Dockerfile, image layer, or build argument;
- a command-line argument, process listing, or shell history;
- Docker or application environment variables;
- logs, screenshots, crash dumps, or result artifacts; or
- the durable authorization ledger.

Repository policy forbids reading, printing, searching, copying, diffing, or summarizing dotenv secrets. The application must not dump the complete environment. Configuration checks may expose only a redacted Boolean such as `configured=true`.

If key exposure is possible, revoke or rotate it immediately. Deleting the visible copy is not revocation.

The runner fixes `https://api.openai.com/v1/responses`, disables redirects and HTTP environment/proxy trust, and rejects alternate OpenAI/Azure endpoint configuration. No Azure login is required for this direct OpenAI path.

## Gold-data isolation

The generation container mounts only:

- `generation_manifest.json`;
- `generation_cases.jsonl`; and
- `split_assignments.json`.

Those files contain source text and split lineage, not logical forms. Development and locked-test references are separate files and are not present in the paid container.

Scoring runs later with Docker network mode `none`, a read-only sealed run mount, one split's read-only references/coverage, and a separate writable report directory. The scorer contains no HTTP/OpenAI transport import.

This protects against runtime gold leakage. It does not prove that a model never saw the public LLF corpus during training.

## Frozen provider call

The only enabled Real-v1 paid lane is direct LLF extraction. The request contract fixes:

- official Responses endpoint;
- requested model `gpt-5.6-luna`;
- `store=false`;
- one exact reasoning profile: historical/default and locked-compatible `none`, or versioned development-only `medium`;
- service tier `default`;
- no tools;
- 2,048 maximum output tokens with `none`, or 32,768 maximum output/reasoning tokens with `medium`;
- sequential execution;
- zero SDK retries and zero application retries; and
- a 60-second total application deadline with `none`, or 240 seconds with `medium`.

The fields above are cross-validated as exact tuples: `none=(2048, 60)` and `medium=(32768, 240)`. The medium profile is a paired development diagnostic and cannot advance the locked-none lane; locked execution remains fixed to `none`.

The provider returns one `logical_form` string. Local trusted code then enforces stricter size limits, parses it through the no-exec allowlist, and records only bounded safe provenance/failure fields.

The model name is an alias and may drift. Requested/returned model labels, response object, service tier, response-ID hash, usage, and latency are evidence about the observed response—not immutable-weight attestation.

## Planning and one-time authorization

The paid chain is deliberately longer than one confirmation flag:

```text
static preregistration
  -> exact offline plan
  -> public one-execution binding
  -> fresh exact authorization
  -> external one-time claim + local consumption
  -> per-ordinal external attempt claim
  -> provider call
  -> sealed outcome
```

The plan freezes dataset/cases, configuration, prompt/schema/parser, implementation and dependency hashes, SDK version, exact image ID, rates, reservations, cap, and expiry. The public execution binding adds the intended run/authorization IDs, output and external-state path hashes, one-execution rule, and rerun policies.

Fresh authorization must reproduce every binding and exact acknowledgement. An earlier general statement such as “up to EUR 15” is not substituted for the exact execution artifact.

At first use, the runner creates an exclusive authorization claim outside the output tree and a matching local consumption file. Recovery requires both and requires their contents to match. Only one existing, a changed path, a copied/deleted run, or a stale/tampered artifact fails closed.

Before each ordinal, a separate append-only external attempt claim and matching local pending artifact are sealed. Once an attempt starts, its conservative reservation is consumed even if the provider times out or returns unusable data. A crash becomes an explicit interrupted failure; the ordinal is not called again.

These controls reduce accidental duplicate execution. They do not guarantee disk `fsync` durability or prevent charges from unrelated use of the same provider account.

## Canary cost boundary

The frozen 2026-09-02 price snapshot records:

| Token category | USD per million |
|---|---:|
| Uncached input | 0.20 |
| Cached input | 0.02 |
| Cache-write input | 0.25 |
| Output | 1.20 |

The historical/default `none` canary reserves 16,384 input tokens and 2,048 output tokens, or `USD 0.006553600` per case. Its 25 cases reserve `USD 0.163840000` beneath an exact `USD 0.170000000` application authorization cap. The versioned `medium` experiment keeps the same input reservation but allows 32,768 output/reasoning tokens, reserving `USD 0.043417600` per case and `USD 1.085440000` total beneath an exact `USD 1.250000000` cap. Mixed profile tuples are rejected.

The snapshot is valid only through `2026-09-02T23:59:59Z`. Planning or execution outside it must stop and use a newly reviewed snapshot. The application cap is not an OpenAI account cap. A request can be billable despite a timeout or failure, provider accounting may differ from local token categories, and other account activity is outside this repository.

After a run, reconcile response IDs, token usage, and billing independently in the provider dashboard. Report both the conservative consumed authorization and the usage-priced calculation. Do not call either a provider invoice.

## Locked cost boundary

The current source code defines a future locked-test reservation for 1,800 cases and an `USD 11.800000000` cap. It is not a current plan or authorization.

At 60 seconds per remaining case, the conservative maximum is 30 hours. That cannot fit the current four-hour plan lifetime or two-hour authorization lifetime, and the current one-day rate snapshot expires. Locked execution is therefore mechanically unavailable until:

1. every canary gate passes;
2. prices are reviewed and refrozen;
3. validity windows are redesigned to cover the full conservative duration;
4. a new exact locked plan is published; and
5. the user gives separate explicit authorization.

The runner must fail closed rather than weaken these requirements.

## Failure and rerun policy

Any provider/configuration failure aborts the canary and fails advancement. HTTP 429 is treated as a real failed attempt, not automatically retried. Refusal, truncation, timeout, invalid schema, malformed LLF, missing/duplicate response ID, incomplete provider identity, usage omission, path mismatch, or artifact inconsistency remains visible.

The static preregistration and exact execution binding prohibit optional stopping:

- **quality failure:** change the configuration only under a new versioned preregistration;
- **operational rerun:** publish a new one-execution binding, obtain fresh authorization, and disclose every attempt; and
- **success:** score once and apply the frozen conjunctive decision.

Repeatedly authorizing the same nondeterministic configuration and publishing the best sample is not permitted.

## Artifact integrity and evidence claims

Artifacts use canonical serialization and SHA-256 to bind their internal lineage. The runner/scorer reject unexpected direct children, symlinks, nonregular files, duplicate ordinals/response IDs, changed request hashes, inconsistent timestamps, incomplete prefixes, and conflicting terminal state.

These controls support statements such as “this report reproduces from these source, code, configuration, and run artifacts.” They do not support statements such as “the provider cryptographically attested to these exact weights.” Provider response identifiers and dashboard records are independent evidence and should be retained/reconciled according to provider policy.

Before publishing, inspect artifacts for secrets, raw identifiers where hashes are intended, machine paths, account/tenant/subscription details, diagnostic dumps, and private input.

## Legacy service security

The FastAPI service has no authentication, tenant isolation, public rate limit, TLS termination, production ingress policy, or global pre-parse raw-body cap. Keep it on loopback or a trusted private network in mock mode. Do not expose PostgreSQL, Redis, Prometheus, Grafana, or worker metrics publicly.

The single worker implements atomic claim/acknowledgement, restart recovery, request/contract validation, and a bounded dead-letter list. PostgreSQL and Redis are not a distributed transaction. The correct claim is at-least-once delivery with fail-closed validation and best-effort idempotency.

Prometheus metrics are implemented with bounded labels. Raw trial IDs, source text, URLs, prompts, credentials, exception messages, and job IDs must not become labels. OpenTelemetry application tracing and verified trace scrubbing remain future work.

Compose/kind/Helm database dependencies are disposable demonstrations without production backup, high availability, retention, or secret management. NetworkPolicy manifests require a capable CNI and are not proof of enforcement on their own.

## Historical Azure evidence

The 2026-09-01 evidence includes:

- an ephemeral mock-only AKS proof with health/readiness, sync/async mock workload, worker, persistence, metrics, and verified destruction of both resource groups; and
- one successful no-ingress Container Apps synthetic Luna smoke using an immutable image, zero platform retries, a user-assigned identity, and Key Vault secret reference.

The dated record stated that the Container Apps Job remained deployed but idle after that proof. Current cloud state must be checked before making a present-tense claim. Its EUR 15 Azure budget alert was delayed notification, not a hard cap or automatic cleanup.

Those exercises demonstrate infrastructure and secret-reference plumbing. They are not Real-v1 quality evidence, clinical validation, a public service, or authorization for another paid run.

## Release checklist

1. Confirm the tree contains no key, dotenv data, cloud credential/ID, Terraform state/plan, kubeconfig, private fixture, or machine-specific path.
2. Reproduce the LLF import, full/split parser coverage, BM25, agreement report, preregistration, static tests, and exact report bytes.
3. Confirm GraphV2 paid planning remains disabled and API/worker/CI remain mock-only.
4. Build, scan, and identify the exact container image bytes used for planning and execution.
5. Publish the static preregistration, exact plan, and one-execution binding before requesting authorization.
6. Verify current official model availability/prices and that the entire conservative duration fits price, plan, and authorization windows.
7. Obtain fresh exact authorization; enter the key only through the hidden prompt.
8. Retain all attempt/outcome/failure artifacts; do not retry or curate outcomes.
9. Score with networking disabled and apply every frozen gate conjunctively.
10. Reconcile usage/billing with the provider dashboard and publish limitations beside any result.
