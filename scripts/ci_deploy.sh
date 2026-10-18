#!/usr/bin/env bash
# Deploy one commit on the VPS. Run by scripts/deploy_entry.sh (the CI deploy
# key's forced command); by hand: `bash scripts/ci_deploy.sh <sha>` as poi.
#
#   1. check out <sha>; 2. build api + admin while the old stack keeps serving;
#   3. wait for in-flight ingest jobs to drain — restarting the worker drops
#      their dramatiq messages and strands the rows as `pending`;
#   4. migrate; 5. `up -d`; 6. reload caddy if its config changed;
#   7. health-check, and roll the code back to the previous commit on failure.
#      Migrations are not rolled back — keep them backward compatible.
#
# VPS-only config stays out of the repo (it is public):
#   .env                        secrets
#   docker-compose.local.yml    optional extra override, e.g. caddy joining a
#                               network another product on this host owns
#   docker/caddy/sites/*.caddy  extra sites imported by the Caddyfile
set -euo pipefail

SHA="${1:?usage: ci_deploy.sh <commit-sha>}"
cd "${POI_LAKE_DIR:-/opt/poi-lake}"

exec 9>/tmp/poi-lake-deploy.lock
flock -n 9 || { echo "another deploy is running" >&2; exit 1; }

log() { echo "[deploy $(date -u +%H:%M:%S)] $*"; }

COMPOSE=(docker compose -f docker-compose.yml -f docker-compose.prod.yml -f docker-compose.caddy.yml)
[[ -f docker-compose.local.yml ]] && COMPOSE+=(-f docker-compose.local.yml)

PREV="$(git rev-parse HEAD)"

wait_for_drain() {
    local n
    for _ in $(seq 1 60); do
        n="$("${COMPOSE[@]}" exec -T postgres psql -U "${POSTGRES_USER:-poi}" -d "${POSTGRES_DB:-poi_lake}" -Atc \
            "SELECT count(*) FROM ingestion_jobs WHERE status IN ('pending','running')")"
        [[ "$n" == "0" ]] && return 0
        log "waiting: $n ingest jobs in flight"
        sleep 10
    done
    log "jobs still in flight after 10 min — restarting anyway"
}

healthy() {
    local code
    for _ in $(seq 1 24); do
        code="$("${COMPOSE[@]}" exec -T api curl -s -o /dev/null -w '%{http_code}' \
            http://localhost:8000/health/ready || true)"
        [[ "$code" == "200" ]] && return 0
        sleep 5
    done
    return 1
}

release() {
    local target="$1" from
    from="$(git rev-parse HEAD)"
    git checkout -q --detach -f "$target"
    log "building images for $(git log -1 --format='%h %s')"
    "${COMPOSE[@]}" build -q api admin
    wait_for_drain
    log "migrating"
    "${COMPOSE[@]}" run --rm -T api alembic upgrade head
    log "starting"
    "${COMPOSE[@]}" up -d --remove-orphans
    # A changed compose file recreates caddy above; a changed Caddyfile alone
    # does not, so reload it — after validating, so a typo cannot take every
    # site on this host down.
    if ! git diff --quiet "$from" "$target" -- docker/caddy; then
        log "caddy config changed — validating and reloading"
        "${COMPOSE[@]}" exec -T caddy caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile
        "${COMPOSE[@]}" exec -T caddy caddy reload --config /etc/caddy/Caddyfile --adapter caddyfile
    fi
}

log "deploying $SHA (current $PREV)"
release "$SHA"

if healthy; then
    log "healthy — deployed $(git rev-parse --short HEAD)"
    exit 0
fi

log "health check FAILED — rolling back to $PREV"
"${COMPOSE[@]}" logs --tail 50 api >&2 || true
release "$PREV"
healthy && log "rolled back to $(git rev-parse --short HEAD)" || log "rollback is unhealthy too"
exit 1
