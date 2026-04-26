#!/usr/bin/env bash
# Phase 7 — postgres backup.
#
# Run from the host while the stack is up:
#   ./scripts/backup_postgres.sh                    # local file
#   S3_BUCKET=poi-lake-backups ./scripts/backup_postgres.sh    # + upload to S3
#
# Designed to be called from cron / a backup container. Uses ``pg_dump
# --format=custom`` so restores can be selective (``pg_restore --table``
# to recover a single table without touching the rest).

set -euo pipefail

# --- config (override via env) -------------------------------------------

PG_HOST="${PG_HOST:-localhost}"
PG_PORT="${PG_PORT:-5432}"
PG_USER="${PG_USER:-poi}"
PG_DB="${PG_DB:-poi_lake}"
BACKUP_DIR="${BACKUP_DIR:-./backups}"
RETENTION_DAYS="${RETENTION_DAYS:-14}"
COMPOSE_SERVICE="${COMPOSE_SERVICE:-postgres}"  # if non-empty, exec inside this container

# Optional S3 destination — set to enable upload.
S3_BUCKET="${S3_BUCKET:-}"
S3_PREFIX="${S3_PREFIX:-poi-lake/postgres}"

# --- run -----------------------------------------------------------------

mkdir -p "$BACKUP_DIR"
TS="$(date -u +%Y%m%dT%H%M%SZ)"
FILE="$BACKUP_DIR/poi_lake_${TS}.dump"

echo "[$(date -u +%H:%M:%SZ)] dumping ${PG_DB} → ${FILE}"

if [[ -n "$COMPOSE_SERVICE" ]]; then
    # Stream pg_dump from the running container straight into the host file.
    docker compose exec -T -e PGPASSWORD "$COMPOSE_SERVICE" \
        pg_dump --username="$PG_USER" --dbname="$PG_DB" \
        --format=custom --compress=9 --no-owner --no-privileges \
        > "$FILE"
else
    PGPASSWORD="${PG_PASSWORD:-poi}" pg_dump \
        --host="$PG_HOST" --port="$PG_PORT" \
        --username="$PG_USER" --dbname="$PG_DB" \
        --format=custom --compress=9 --no-owner --no-privileges \
        --file="$FILE"
fi

SIZE_BYTES="$(stat -c '%s' "$FILE" 2>/dev/null || stat -f '%z' "$FILE")"
echo "[$(date -u +%H:%M:%SZ)] dump complete: ${SIZE_BYTES} bytes"

# --- optional S3 upload --------------------------------------------------

if [[ -n "$S3_BUCKET" ]]; then
    if ! command -v aws >/dev/null 2>&1; then
        echo "[ERROR] S3_BUCKET set but aws CLI not on PATH" >&2
        exit 2
    fi
    KEY="${S3_PREFIX}/poi_lake_${TS}.dump"
    echo "[$(date -u +%H:%M:%SZ)] uploading to s3://${S3_BUCKET}/${KEY}"
    aws s3 cp "$FILE" "s3://${S3_BUCKET}/${KEY}" \
        --storage-class STANDARD_IA \
        --metadata "source=poi-lake,db=${PG_DB},timestamp=${TS}"
fi

# --- prune local copies older than RETENTION_DAYS -----------------------

if [[ "$RETENTION_DAYS" -gt 0 ]]; then
    find "$BACKUP_DIR" -name 'poi_lake_*.dump' -type f \
        -mtime "+${RETENTION_DAYS}" -print -delete || true
fi

echo "[$(date -u +%H:%M:%SZ)] done"
