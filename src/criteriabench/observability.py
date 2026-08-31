"""Low-cardinality Prometheus metrics for operational and cost visibility."""

from prometheus_client import Counter, Histogram

from criteriabench.providers.base import ProviderResult

HTTP_REQUESTS = Counter(
    "criteriabench_http_requests_total",
    "HTTP requests handled by the API",
    ("method", "route", "status"),
)
HTTP_DURATION = Histogram(
    "criteriabench_http_request_duration_seconds",
    "HTTP request duration in seconds",
    ("method", "route"),
)
EXTRACTIONS = Counter(
    "criteriabench_extractions_total",
    "Eligibility extraction attempts",
    ("provider", "status"),
)
EVALUATIONS = Counter(
    "criteriabench_evaluations_total",
    "Typed extraction evaluations",
    ("persisted",),
)
TOKENS = Counter(
    "criteriabench_tokens_total",
    "Tokens reported by paid extraction providers",
    ("provider", "direction"),
)
ESTIMATED_COST = Counter(
    "criteriabench_estimated_cost_usd_total",
    "Estimated API cost in US dollars",
    ("provider",),
)
EXTRACTION_DURATION = Histogram(
    "criteriabench_extraction_duration_seconds",
    "Provider extraction duration in seconds",
    ("provider", "model"),
)


def record_provider_result(result: ProviderResult) -> None:
    """Record one validated provider result in any execution surface."""

    EXTRACTIONS.labels(result.provider, "completed").inc()
    TOKENS.labels(result.provider, "input").inc(result.usage.input_tokens)
    TOKENS.labels(result.provider, "output").inc(result.usage.output_tokens)
    ESTIMATED_COST.labels(result.provider).inc(result.estimated_cost_usd)
    EXTRACTION_DURATION.labels(result.provider, result.model).observe(result.latency_ms / 1_000)
