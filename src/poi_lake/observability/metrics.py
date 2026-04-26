"""Prometheus metrics — single REGISTRY shared by app + middleware + workers.

Cardinality is intentionally low:
  * HTTP labels are (method, route_template, status_class) — never the raw
    path, so ``/master-pois/<uuid>`` doesn't blow the cardinality budget.
  * Pipeline labels are bounded enums (source code, decision name).
"""

from __future__ import annotations

from prometheus_client import (
    CONTENT_TYPE_LATEST,
    CollectorRegistry,
    Counter,
    Gauge,
    Histogram,
    generate_latest,
)

# We use the default registry; multi-process collection is opted into
# downstream by setting PROMETHEUS_MULTIPROC_DIR (Phase 7+ deployment).
REGISTRY = CollectorRegistry()


# ---- HTTP --------------------------------------------------------------

HTTP_REQUESTS = Counter(
    "poi_lake_http_requests_total",
    "HTTP requests served by the API.",
    labelnames=("method", "route", "status"),
    registry=REGISTRY,
)
HTTP_REQUEST_DURATION = Histogram(
    "poi_lake_http_request_duration_seconds",
    "HTTP request latency.",
    labelnames=("method", "route"),
    buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0),
    registry=REGISTRY,
)


# ---- Pipeline -----------------------------------------------------------

INGEST_RAW_INSERTED = Counter(
    "poi_lake_ingest_raw_inserted_total",
    "raw_pois rows inserted by the IngestionService, by source.",
    labelnames=("source",),
    registry=REGISTRY,
)
INGEST_RAW_DUPLICATES = Counter(
    "poi_lake_ingest_raw_duplicates_total",
    "raw_pois inserts skipped due to content_hash dedup, by source.",
    labelnames=("source",),
    registry=REGISTRY,
)
INGEST_ERRORS = Counter(
    "poi_lake_ingest_errors_total",
    "raw_pois insert errors during a job, by source.",
    labelnames=("source",),
    registry=REGISTRY,
)

NORMALIZE_PROCESSED_INSERTED = Counter(
    "poi_lake_normalize_processed_inserted_total",
    "processed_pois rows inserted by the NormalizePipeline, by source.",
    labelnames=("source",),
    registry=REGISTRY,
)
NORMALIZE_SKIPPED = Counter(
    "poi_lake_normalize_skipped_total",
    "raw_pois skipped during normalize (no name / no coords / unknown source), by source + reason.",
    labelnames=("source", "reason"),
    registry=REGISTRY,
)

DEDUPE_DECISIONS = Counter(
    "poi_lake_dedupe_decisions_total",
    "Pairwise dedupe routing outcome.",
    labelnames=("decision",),  # auto_merge | needs_llm | distinct
    registry=REGISTRY,
)
MERGE_MASTERS_CREATED = Counter(
    "poi_lake_merge_masters_created_total",
    "master_pois rows created by MergeService.",
    registry=REGISTRY,
)
MERGE_MEMBERS = Counter(
    "poi_lake_merge_members_total",
    "processed_pois rows folded into a master (sum across runs).",
    registry=REGISTRY,
)


# ---- LLM ----------------------------------------------------------------

LLM_CALLS = Counter(
    "poi_lake_llm_calls_total",
    "Anthropic Claude API calls, by model and outcome.",
    labelnames=("model", "outcome"),  # outcome: cached | hit | error
    registry=REGISTRY,
)
LLM_TOKENS = Counter(
    "poi_lake_llm_tokens_total",
    "Tokens billed against the Claude API, by model and direction.",
    labelnames=("model", "direction"),  # direction: input | output
    registry=REGISTRY,
)


# ---- Queue depth (set by a periodic scrape on the API container) -------

QUEUE_DEPTH = Gauge(
    "poi_lake_queue_depth",
    "Pending dramatiq messages, by queue.",
    labelnames=("queue",),
    registry=REGISTRY,
)


def render_metrics() -> tuple[bytes, str]:
    """Return ``(body, content_type)`` for the /metrics endpoint."""
    return generate_latest(REGISTRY), CONTENT_TYPE_LATEST
