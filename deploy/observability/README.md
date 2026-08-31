# Optional local observability

The default Compose stack exposes application metrics but does not start a monitoring UI. Use the canonical no-dotenv wrapper to enable the optional profile:

```powershell
.\scripts\compose-safe.ps1 --profile observability up --build -d --wait
```

Endpoints:

- API metrics: <http://127.0.0.1:8000/metrics>
- Prometheus: <http://127.0.0.1:9091>
- Grafana: <http://127.0.0.1:3000>

Prometheus scrapes the API and worker metrics service. Grafana is provisioned with a local CriteriaBench dashboard and is bound to loopback. The configuration is a developer demonstration, not a public or multi-tenant monitoring deployment.

The repository also contains an OpenTelemetry Collector configuration, but the application does **not** currently emit supported OpenTelemetry traces. Do not claim distributed tracing until application instrumentation, attribute/header scrubbing, a backend, and receipt tests are implemented.

Stop the stack without deleting the PostgreSQL volume:

```powershell
.\scripts\compose-safe.ps1 --profile observability down
```

Do not place trial IDs, source text, prompts, credentials, full URLs, exception messages, or job UUIDs in labels. Keep label sets bounded.
