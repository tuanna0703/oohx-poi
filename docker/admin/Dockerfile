FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PATH="/install/bin:${PATH}" \
    PYTHONPATH="/app/src"

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl tini \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --system --gid 1000 poi \
    && useradd  --system --uid 1000 --gid poi --create-home --shell /bin/bash poi

WORKDIR /app

COPY pyproject.toml README.md ./
COPY src ./src

RUN pip install --prefix=/install --no-cache-dir ".[admin]"

COPY --chown=poi:poi admin ./admin

USER poi
EXPOSE 8501

ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["streamlit", "run", "admin/app.py", "--server.address=0.0.0.0", "--server.port=8501", "--server.headless=true"]
