# CriteriaBench learning guide

This guide explains the infrastructure around CriteriaBench for someone who already knows Python/data/AI but is new to cloud-native engineering.

## Start with the mental model

CriteriaBench has two ordinary Python processes:

- an **API**, which receives requests and returns status/results; and
- a **worker**, which performs queued mock extraction in the background.

They share:

- **PostgreSQL**, the durable record of requests, states, and results; and
- **Redis**, the short-lived work queue between API and worker.

Everything else answers one of four questions:

1. How do we package the processes consistently? **Docker**.
2. How do we run several packages together? **Compose** on one computer; **Kubernetes** in a cluster.
3. How do we describe/reuse the configuration? **Helm** for Kubernetes; **Terraform** for cloud resources.
4. How do we prove and observe behavior? **GitHub Actions**, tests, health probes, logs, and **Prometheus/Grafana**.

## Docker: package one process

A container image is a versioned filesystem plus instructions for starting a process. It avoids “works on my machine” differences by shipping the Python runtime and locked dependencies with the application.

CriteriaBench uses a multi-stage Dockerfile:

- the builder consumes `uv.lock` and compiles/installs dependencies;
- the runtime copies only the resulting environment and migrations;
- the application runs as an unprivileged user, not root; and
- the image defaults to the free mock provider.

You should be able to explain image versus container, build layers/cache, digest versus mutable tag, build context, non-root users, and health checks. See Docker's [container overview](https://docs.docker.com/get-started/docker-concepts/the-basics/what-is-a-container/).

## Compose: run the local system

Compose describes several containers, networks, volumes, health checks, and start-order dependencies in one YAML file. Here it starts PostgreSQL, Redis, a one-shot migration, API, and worker; monitoring is optional.

Use the project wrapper because normal Compose can load a root `.env` implicitly:

```powershell
.\scripts\compose-safe.ps1 up --build -d --wait
.\scripts\compose-safe.ps1 ps
.\scripts\compose-safe.ps1 logs --tail 100 api
.\scripts\compose-safe.ps1 logs --tail 100 worker
.\scripts\compose-safe.ps1 down
```

The API is published only on `127.0.0.1:8000`. PostgreSQL and Redis stay on the private backend network in the normal stack.

## PostgreSQL and migrations

PostgreSQL is the source of durable run state. SQLAlchemy provides the async repository layer; Alembic versions schema changes.

The migration runs before API/worker start. Tests exercise upgrade, downgrade, upgrade, and a repository round-trip against real PostgreSQL. This matters because SQLite unit tests cannot prove PostgreSQL types, drivers, connections, or migrations.

Concepts to learn: transaction, primary/foreign key, migration revision/head, connection URL, async connection pool, and rollback.

## Redis and background jobs

The API can respond `202 Accepted` after saving/enqueuing a job. The worker atomically claims it, processes it, persists a terminal state, then acknowledges it.

If the worker dies after claim, the item remains in a processing list and is recovered on worker restart. Invalid envelopes go to a bounded dead-letter list. This is **at least once**: code must tolerate a job being seen again. PostgreSQL and Redis are not one transaction.

Concepts to learn: queue, producer/consumer, acknowledgement, idempotency, dead-letter queue, crash recovery, and backpressure.

## Kubernetes: keep containers in a desired state

Kubernetes is a reconciliation system. You declare the desired state; controllers keep trying to make reality match it.

CriteriaBench uses:

- a **Deployment** for API, Redis, and worker;
- a **StatefulSet** for disposable demo PostgreSQL;
- a **Job** for the database migration;
- **Services** for stable network names;
- **ConfigMap/Secret** objects for non-secret/secret-shaped configuration;
- readiness/liveness probes;
- resource requests/limits and restrictive security contexts; and
- NetworkPolicy definitions.

The local proof uses **kind** (“Kubernetes in Docker”):

```powershell
.\scripts\kind-up.ps1
# http://127.0.0.1:8080/docs
.\scripts\kind-down.ps1 -Confirmation DELETE-KIND
```

The script rebuilds/loads the local image, reapplies Kustomize, reruns migration, restarts app pods, and waits for readiness. The demo is loopback-only and ephemeral. A default kind CNI is not production network-policy evidence.

Read the official [Kubernetes overview](https://kubernetes.io/docs/concepts/overview/) and learn Pod, Deployment, Service, Job, namespace, probe, request/limit, rollout, and `kubectl logs/describe`.

## Kustomize versus Helm

Both produce Kubernetes YAML:

- **Kustomize** layers patches on base manifests. CriteriaBench uses a kind overlay to add disposable PostgreSQL/Redis and a loopback NodePort.
- **Helm** renders templates from values. The chart supports a release-scoped image/digest, database Secret, migration Job, probes, policies, and optional ephemeral demo dependencies.

Helm is a packaging/configuration tool, not a cluster. A successful `helm lint` proves template rules; a runtime install/wait/smoke/uninstall proves more.

See the [Helm chart guide](https://helm.sh/docs/topics/charts/).

## Terraform and Azure

Terraform describes cloud resources in versioned configuration and creates a reviewable **plan** before **apply**. It maintains **state**, which can be sensitive and must not be committed.

CriteriaBench defines a deliberately small AKS proof: a parent and managed-node resource group, AKS free control-plane SKU, one worker node, modern networking/Cilium, Entra/Azure RBAC settings, budget alerts, and TTL tags. The control plane may be free, but the node/disk/network are billable.

The safe path is preflight → saved plan/hash review → explicit billing approval → apply the exact plan with an immutable image digest → collect mock evidence → immediate destroy → verify both parent and AKS node resource groups are gone.

An explicitly approved ephemeral mock-only proof followed that path on 2026-09-01 with immutable image `sha256:a23de765a424d74d205f84e4255d572ab5cc79bd7774af034cfa9dca804d8ba2`. AKS health and readiness were up; sync extraction returned 200; async extraction returned 202 and the worker completed; the result contained one inclusion and one exclusion criterion under schema 1.0 with zero tokens and USD 0 cost; and API and worker metrics were observed.

Teardown was independently confirmed: the parent and managed-node resource groups and the budget were absent, Terraform retained only data-source entries and no managed resources, and temporary proof artifacts were absent. No Azure deployment currently exists, and the proof was not a production deployment. Budget alerts are not caps.

Read HashiCorp's [Terraform introduction](https://developer.hashicorp.com/terraform/intro) and learn provider, resource/data source, variable/output, plan, state, drift, and destroy.

## GitHub Actions: a remote proof gate

CI runs on every proposed change and should answer: does this exact revision pass?

CriteriaBench's workflow:

- installs from the frozen lock;
- runs Ruff, formatting, strict mypy, and tests;
- exercises real PostgreSQL/Redis services;
- generates/uploads the zero-cost benchmark artifact;
- validates canonical Compose, Helm, Kustomize, and Terraform;
- builds/scans the container; and
- blocks publication on failures.

The publish workflow builds once, scans the saved image archive, transfers/verifies the exact bytes, pushes those bytes, checks one registry digest, and attests it. It never rebuilds after the scan gate.

Learn trigger, job, step, service container, artifact, permissions, concurrency, cache, secret, and immutable action pin. See [GitHub Actions documentation](https://docs.github.com/en/actions).

## Observability

Three common signals are:

- **metrics**: numbers over time (request count/latency, extraction outcome, cost);
- **logs**: discrete safe events for diagnosis; and
- **traces**: one request's path across components.

CriteriaBench implements Prometheus metrics and safe logs. Grafana can visualize the scraped metrics. OpenTelemetry application tracing is future work; a collector configuration alone does not mean traces exist.

Useful questions:

- Is `/readyz` failing because PostgreSQL or Redis is down?
- Did extraction failures or latency increase?
- Is the queue/worker making progress?
- Did token usage or usage-priced cost change?

Never put high-cardinality IDs, source text, prompts, credentials, or exception contents in metric labels.

## Security and cost reasoning

The main boundary is that API, worker, CI, Compose, Kubernetes, and Azure chart are mock-only. Only the explicit local benchmark wrapper can read the ignored key and start a paid child after all flags and a budget are supplied.

Controls to explain:

- least privilege/non-root/read-only filesystems;
- loopback/private networking for an unauthenticated API;
- no implicit dotenv discovery;
- fixed external hosts and input manifests;
- immutable dependency/image/action references;
- plan/hash/digest binding for cloud apply; and
- alerts plus prompt teardown rather than pretending a pay-as-you-go budget is a hard cap.

GitHub-to-Azure OIDC, Key Vault, remote state, and end-to-end workload identity remain future production work.

## Suggested learning order

1. Run source tests and read the API/schema/evaluator.
2. Build the locked image; inspect its user/environment/health check.
3. Run Compose; send sync and async mock requests; inspect DB/Redis/metrics.
4. Read and run the real service integration tests.
5. Render Kustomize and Helm.
6. Run the disposable kind proof and diagnose a pod with `get`, `describe`, and `logs`.
7. Install/uninstall the Helm demo in kind.
8. Read CI/publish workflows and follow one artifact/digest through them.
9. Run Terraform offline validation and read a generated plan only after Azure login is ready.
10. Perform a short Azure proof or tiny live-model run only after separate cost approval.

## Honest CV/interview phrasing

Supported now:

> Built a typed AI extraction/evaluation service with FastAPI, PostgreSQL and Redis; locked and containerized it with Docker; exercised mock sync/async workloads through Compose, Kustomize/kind, and Helm; and added CI, security gates, health/readiness probes, Prometheus metrics, failure recovery, provenance, and cost accounting.

Also supported:

> Executed and destroyed an explicitly approved ephemeral mock-only one-node AKS proof with modern networking, plan/hash/digest binding, budget alerts, health/readiness, sync/async workload evidence, API/worker metrics, and verified teardown.

Not supported: “production service,” “clinically validated,” “currently deployed to Azure,” “production Azure deployment,” “exactly once,” “OpenTelemetry tracing,” “OIDC/Key Vault integration,” or “improved model accuracy.” Two approved guarded Luna attempts—the initial attempt and one retry—both failed closed with `ProvenanceError`; no successful or scored live-model result, and no zero-provider-billing claim, is made.
