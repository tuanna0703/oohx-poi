#!/usr/bin/env bash
# Apply / update poi-lake on the VPS.
#
# Usage:
#   ./scripts/deploy.sh                 # pull + rebuild + rolling restart
#   ./scripts/deploy.sh --first-run     # also runs alembic + seed_all
#   ./scripts/deploy.sh --no-pull       # rebuild from local source (CI artifact)
#
# Run from $INSTALL_DIR (default /opt/poi-lake) as the ``poi`` user.

set -euo pipefail

cd "$(dirname "$0")/.."

FIRST_RUN=0
DO_PULL=1
for arg in "$@"; do
    case "$arg" in
        --first-run) FIRST_RUN=1 ;;
        --no-pull)   DO_PULL=0 ;;
        --help|-h)
            sed -n '2,12p' "$0"; exit 0 ;;
        *)
            echo "unknown flag: $arg" >&2; exit 2 ;;
    esac
done

if [[ ! -f .env ]]; then
    echo "missing .env — copy .env.production.example and fill it in" >&2
    exit 1
fi

# Ensure required secrets are non-empty.
required=(APP_SECRET_KEY POSTGRES_PASSWORD CADDY_API_HOST CADDY_ADMIN_HOST ACME_EMAIL ADMIN_BASIC_AUTH_HASH)
for k in "${required[@]}"; do
    val="$(grep -E "^${k}=" .env | head -1 | cut -d= -f2-)"
    if [[ -z "${val// }" ]]; then
        echo "missing required env: $k (edit .env)" >&2
        exit 1
    fi
done

COMPOSE=(docker compose
    -f docker-compose.yml
    -f docker-compose.prod.yml
    -f docker-compose.caddy.yml)

if [[ $DO_PULL -eq 1 ]]; then
    echo "[deploy] pulling source"
    git pull --ff-only
fi

echo "[deploy] building images"
"${COMPOSE[@]}" build --pull

if [[ $FIRST_RUN -eq 1 ]]; then
    echo "[deploy] first run: starting postgres + redis only for migrations"
    "${COMPOSE[@]}" up -d postgres redis
    # Wait for postgres healthcheck.
    for i in {1..30}; do
        if "${COMPOSE[@]}" exec -T postgres pg_isready -U "${POSTGRES_USER:-poi}" >/dev/null 2>&1; then
            break
        fi
        sleep 2
    done

    echo "[deploy] running alembic upgrade"
    "${COMPOSE[@]}" run --rm api alembic upgrade head

    echo "[deploy] seeding sources + brands + admin units"
    "${COMPOSE[@]}" run --rm api python scripts/seed_all.py

    echo
    echo "Create the first API client for oohx:"
    echo "  ${COMPOSE[*]} run --rm api python scripts/create_api_client.py oohx \\"
    echo "      --permissions read:master --rate-limit 1000"
    echo
fi

echo "[deploy] starting full stack (rolling)"
"${COMPOSE[@]}" up -d --remove-orphans

# Run migrations on every deploy — alembic is idempotent on no-op.
if [[ $FIRST_RUN -eq 0 ]]; then
    echo "[deploy] applying any new migrations"
    "${COMPOSE[@]}" exec -T api alembic upgrade head
fi

echo "[deploy] checking health"
sleep 5
for url in http://localhost:8000/health http://localhost:8000/health/ready; do
    code="$("${COMPOSE[@]}" exec -T api curl -s -o /dev/null -w '%{http_code}' "$url" || true)"
    printf "  %-40s -> %s\n" "$url" "$code"
done

echo "[deploy] done"
