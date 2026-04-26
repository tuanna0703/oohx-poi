#!/usr/bin/env bash
# Restore from a pg_dump --format=custom file produced by backup_postgres.sh.
#
# Usage:
#   ./scripts/restore_postgres.sh ./backups/poi_lake_20260101T000000Z.dump
#
# WARNING: drops the existing schema. Confirm before running in shared envs.

set -euo pipefail

if [[ $# -lt 1 ]]; then
    echo "usage: $0 <dump-file>" >&2
    exit 64
fi

FILE="$1"
PG_HOST="${PG_HOST:-localhost}"
PG_PORT="${PG_PORT:-5432}"
PG_USER="${PG_USER:-poi}"
PG_DB="${PG_DB:-poi_lake}"
COMPOSE_SERVICE="${COMPOSE_SERVICE:-postgres}"

if [[ ! -f "$FILE" ]]; then
    echo "[ERROR] file not found: $FILE" >&2
    exit 65
fi

read -rp "About to RESTORE ${PG_DB} from ${FILE}. Existing data will be dropped. Continue? [y/N] " ans
[[ "${ans,,}" == "y" ]] || { echo "aborted"; exit 0; }

echo "[$(date -u +%H:%M:%SZ)] restoring → ${PG_DB}"

if [[ -n "$COMPOSE_SERVICE" ]]; then
    docker compose exec -T "$COMPOSE_SERVICE" \
        pg_restore --username="$PG_USER" --dbname="$PG_DB" \
        --clean --if-exists --no-owner --no-privileges --verbose \
        < "$FILE"
else
    PGPASSWORD="${PG_PASSWORD:-poi}" pg_restore \
        --host="$PG_HOST" --port="$PG_PORT" \
        --username="$PG_USER" --dbname="$PG_DB" \
        --clean --if-exists --no-owner --no-privileges --verbose \
        "$FILE"
fi

echo "[$(date -u +%H:%M:%SZ)] restore done"
