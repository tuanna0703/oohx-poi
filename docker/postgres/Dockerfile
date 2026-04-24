# Base: pgvector/pgvector (official, pgvector preinstalled) on postgres:16.
# We then layer PostGIS on top via apt — simpler and faster than compiling
# pgvector from source (which needs clang-13 that's not in the postgis image).
FROM pgvector/pgvector:pg16

ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        postgresql-16-postgis-3 \
        postgresql-16-postgis-3-scripts \
    && rm -rf /var/lib/apt/lists/*

# Enable extensions at DB init
COPY init-extensions.sql /docker-entrypoint-initdb.d/10-extensions.sql
