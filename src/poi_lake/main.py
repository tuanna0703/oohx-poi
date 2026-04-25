"""FastAPI application entrypoint.

Phase 1 exposes only ``/health`` and ``/health/ready``. The ``/api/v1/*``
endpoints are added in Phase 5.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, status
from fastapi.responses import JSONResponse
from sqlalchemy import text

from poi_lake import __version__
from poi_lake.api.v1 import api_v1_router
from poi_lake.config import get_settings
from poi_lake.db import get_engine

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):  # noqa: ARG001
    settings = get_settings()
    logging.basicConfig(level=settings.app_log_level)
    logger.info("poi-lake starting: env=%s version=%s", settings.app_env, __version__)
    yield
    await get_engine().dispose()
    logger.info("poi-lake stopped")


app = FastAPI(
    title="POI Data Lake",
    version=__version__,
    description="Multi-source POI ingestion + AI dedup/normalization + curated REST API.",
    lifespan=lifespan,
)
app.include_router(api_v1_router)


@app.get("/health", tags=["health"])
async def health() -> dict[str, Any]:
    """Liveness probe — cheap, no external dependencies."""
    return {"status": "ok", "version": __version__}


@app.get("/health/ready", tags=["health"])
async def health_ready() -> JSONResponse:
    """Readiness probe — verifies DB + required extensions are up."""
    checks: dict[str, Any] = {"database": False, "postgis": False, "pgvector": False}
    try:
        engine = get_engine()
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
            checks["database"] = True
            result = await conn.execute(
                text("SELECT extname FROM pg_extension WHERE extname IN ('postgis', 'vector')")
            )
            installed = {row[0] for row in result}
            checks["postgis"] = "postgis" in installed
            checks["pgvector"] = "vector" in installed
    except Exception as exc:
        logger.warning("readiness probe failed: %s", exc)
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content={"status": "degraded", "checks": checks, "error": str(exc)},
        )

    all_ok = all(checks.values())
    return JSONResponse(
        status_code=status.HTTP_200_OK if all_ok else status.HTTP_503_SERVICE_UNAVAILABLE,
        content={"status": "ready" if all_ok else "degraded", "checks": checks},
    )
