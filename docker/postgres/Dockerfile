# PostgreSQL 16 + PostGIS 3.4 + pgvector (built from source to guarantee version)
FROM postgis/postgis:16-3.4

ARG PGVECTOR_VERSION=v0.7.4

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        build-essential \
        git \
        ca-certificates \
        postgresql-server-dev-16 \
    && git clone --branch "${PGVECTOR_VERSION}" --depth 1 https://github.com/pgvector/pgvector.git /tmp/pgvector \
    && cd /tmp/pgvector \
    && make OPTFLAGS="" \
    && make install \
    && cd / \
    && rm -rf /tmp/pgvector \
    && apt-get purge -y --auto-remove build-essential git postgresql-server-dev-16 \
    && rm -rf /var/lib/apt/lists/*

# Enable extensions at DB init
COPY init-extensions.sql /docker-entrypoint-initdb.d/10-extensions.sql
