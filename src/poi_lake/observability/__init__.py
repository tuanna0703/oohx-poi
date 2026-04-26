"""Phase 7 — observability glue (logging + metrics)."""

from poi_lake.observability.logging import configure_logging, get_logger
from poi_lake.observability.metrics import (
    DEDUPE_DECISIONS,
    HTTP_REQUESTS,
    HTTP_REQUEST_DURATION,
    INGEST_RAW_INSERTED,
    MERGE_MASTERS_CREATED,
    NORMALIZE_PROCESSED_INSERTED,
)

__all__ = [
    "DEDUPE_DECISIONS",
    "HTTP_REQUESTS",
    "HTTP_REQUEST_DURATION",
    "INGEST_RAW_INSERTED",
    "MERGE_MASTERS_CREATED",
    "NORMALIZE_PROCESSED_INSERTED",
    "configure_logging",
    "get_logger",
]
