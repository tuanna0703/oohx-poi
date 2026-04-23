# ---------- Build stage ----------
FROM python:3.12-slim-bookworm AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /build

COPY pyproject.toml README.md ./
COPY src ./src

RUN pip install --prefix=/install --no-cache-dir .

# ---------- Runtime stage ----------
FROM python:3.12-slim-bookworm AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/install/bin:${PATH}" \
    PYTHONPATH="/app/src"

# Runtime deps: libpq (asyncpg ships wheels, but keep minimal CA + curl for healthchecks)
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl tini \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --system --gid 1000 poi \
    && useradd  --system --uid 1000 --gid poi --create-home --shell /bin/bash poi

COPY --from=builder /install /install

WORKDIR /app
COPY --chown=poi:poi src ./src
COPY --chown=poi:poi migrations ./migrations
COPY --chown=poi:poi alembic.ini ./alembic.ini
COPY --chown=poi:poi scripts ./scripts

RUN mkdir -p /app/models && chown -R poi:poi /app
USER poi

EXPOSE 8000

ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["uvicorn", "poi_lake.main:app", "--host", "0.0.0.0", "--port", "8000"]
