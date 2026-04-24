FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DEFAULT_TIMEOUT=300 \
    PIP_RETRIES=5 \
    PYTHONPATH="/app/src"

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl tini \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --system --gid 1000 poi \
    && useradd  --system --uid 1000 --gid poi --create-home --shell /bin/bash poi

WORKDIR /app

COPY pyproject.toml README.md ./
COPY src ./src

# The admin build pulls the core dependencies too (sqlalchemy, sentence-transformers...).
# Pre-install CPU-only torch to avoid dragging in the ~1.5 GB CUDA stack.
RUN pip install --no-cache-dir \
        --index-url https://download.pytorch.org/whl/cpu \
        "torch==2.5.1" \
    && pip install --no-cache-dir ".[admin]"

COPY --chown=poi:poi admin ./admin

USER poi
EXPOSE 8501

ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["streamlit", "run", "admin/app.py", "--server.address=0.0.0.0", "--server.port=8501", "--server.headless=true"]
