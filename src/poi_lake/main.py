"""FastAPI application entrypoint."""

from __future__ import annotations

import time
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, Request, Response, status
from fastapi.responses import JSONResponse
from sqlalchemy import text
from starlette.middleware.base import BaseHTTPMiddleware

from poi_lake import __version__
from poi_lake.api.v1 import api_v1_router
from poi_lake.config import get_settings
from poi_lake.db import get_engine
from poi_lake.observability import (
    HTTP_REQUEST_DURATION,
    HTTP_REQUESTS,
    configure_logging,
    get_logger,
)
from poi_lake.observability.metrics import render_metrics

logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):  # noqa: ARG001
    settings = get_settings()
    configure_logging(env=settings.app_env, level=settings.app_log_level)
    logger.info("poi-lake starting", env=settings.app_env, version=__version__)
    yield
    await get_engine().dispose()
    logger.info("poi-lake stopped")


class PrometheusMiddleware(BaseHTTPMiddleware):
    """Time every request and increment counters keyed by route template.

    Using ``request.scope['route']`` keeps the cardinality bounded — a path
    like ``/api/v1/master-pois/{master_id}`` is one label value across all
    UUIDs, not millions. Also copies any ``request.state.rate_limit_headers``
    set by the rate-limit dependency onto the response so consumers can
    self-throttle.
    """

    async def dispatch(self, request: Request, call_next):
        start = time.perf_counter()
        try:
            response = await call_next(request)
            status_code = response.status_code
        except Exception:
            status_code = 500
            raise
        finally:
            elapsed = time.perf_counter() - start
            route = request.scope.get("route")
            template = getattr(route, "path", request.url.path) if route else request.url.path
            HTTP_REQUEST_DURATION.labels(request.method, template).observe(elapsed)
            HTTP_REQUESTS.labels(
                request.method, template, _status_class(status_code)
            ).inc()
        rl_headers = getattr(request.state, "rate_limit_headers", None)
        if rl_headers:
            for k, v in rl_headers.items():
                response.headers[k] = v
        return response


def _status_class(code: int) -> str:
    return f"{code // 100}xx"


app = FastAPI(
    title="POI Data Lake",
    version=__version__,
    description="Multi-source POI ingestion + AI dedup/normalization + curated REST API.",
    lifespan=lifespan,
)
app.add_middleware(PrometheusMiddleware)
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
        logger.warning("readiness probe failed", error=str(exc))
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content={"status": "degraded", "checks": checks, "error": str(exc)},
        )

    all_ok = all(checks.values())
    return JSONResponse(
        status_code=status.HTTP_200_OK if all_ok else status.HTTP_503_SERVICE_UNAVAILABLE,
        content={"status": "ready" if all_ok else "degraded", "checks": checks},
    )


@app.get("/metrics", tags=["observability"])
async def metrics() -> Response:
    """Prometheus exposition. Unauthenticated by design — keep the
    container off the public internet, scrape from inside the VPC."""
    body, content_type = render_metrics()
    return Response(content=body, media_type=content_type)
