# ---------- Build stage ----------
FROM python:3.12-slim-bookworm AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_DEFAULT_TIMEOUT=300 \
    PIP_RETRIES=5

RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /build

COPY pyproject.toml README.md ./
COPY src ./src

# Pre-install CPU-only torch from PyTorch's CPU wheel index. sentence-transformers
# depends on torch; without this, pip pulls the full CUDA stack (~1.5 GB, ~900 MB
# for torch alone). CPU torch is enough for MiniLM inference and keeps the image small.
RUN pip install --prefix=/install --no-cache-dir \
        --index-url https://download.pytorch.org/whl/cpu \
        "torch==2.5.1" \
    && PYTHONPATH=/install/lib/python3.12/site-packages \
       pip install --prefix=/install --no-cache-dir ".[dev]"

# ---------- Runtime stage ----------
FROM python:3.12-slim-bookworm AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH="/app/src"

# Runtime deps: libpq (asyncpg ships wheels, but keep minimal CA + curl for healthchecks)
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl tini \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --system --gid 1000 poi \
    && useradd  --system --uid 1000 --gid poi --create-home --shell /bin/bash poi

# Merge builder's /install (PREFIX tree) into /usr/local so uvicorn, alembic,
# and all site-packages are on Python's default search paths.
COPY --from=builder /install /usr/local/

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
