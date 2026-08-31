# Deployment layouts

CriteriaBench provides two Kubernetes paths for different purposes:

- `k8s/base` is the reusable application baseline. It expects a pre-created `criteriabench-database` Secret containing `DATABASE_URL`; it must not contain a cloud database password.
- `k8s/overlays/kind` adds a disposable local database Secret, local images, and a NodePort for the one-machine kind exercise.
- `helm/criteriabench` is the configurable packaging used for the short-lived AKS proof. Its `demoDependencies` option generates a random in-cluster PostgreSQL password at install time and uses ephemeral PostgreSQL/Redis storage. That option is explicitly not a production data layer.

The AKS proof deliberately uses mock LLM mode and `kubectl port-forward`. It does not implement Azure Key Vault, a managed database, public ingress, or a durable production deployment. Those are documented next steps, not current claims.

Production-hardening work beyond the portfolio proof would include managed PostgreSQL/Redis, controlled migrations, a real secrets provider, workload-specific Azure identity federation, backups, multi-zone capacity, and an incident-tested rollback plan.
