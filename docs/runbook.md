# POI Lake — Operational Runbook

Day-to-day playbook for running poi-lake. Assumes you have shell access on
the host, `docker compose` on PATH, and the project checked out at
`oohx-poi/`. All commands run from that directory unless otherwise noted.

## 1. Stack layout

| Service | Image | Port (host) | Notes |
|---|---|---|---|
| `postgres` | poi-lake/postgres (pgvector + postgis) | 5432 | data in `poi_postgres_data` (named volume in prod) |
| `redis` | redis:7.2-alpine | 6379 | dramatiq broker + LLM resolver cache |
| `api` | poi-lake/api | 8000 | FastAPI; exposes `/health`, `/health/ready`, `/metrics`, `/docs` |
| `worker` | poi-lake/api (different command) | 9191 (prom) | Dramatiq consumer for ingest / normalize / dedupe queues |
| `admin` | poi-lake/admin | 8501 | Streamlit ops UI |
| `gosom-scraper` | gosom/google-maps-scraper | 8090 | Google Maps enrichment sidecar |
| `prometheus` (monitoring profile) | prom/prometheus | 9090 | scrapes api + worker every 15s |
| `grafana` (monitoring profile) | grafana/grafana | 3000 | anonymous viewer; the `POI Lake — Pipeline` dashboard is auto-provisioned |

## 2. Bring up / tear down

```bash
# dev
docker compose up -d

# prod
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d

# prod + monitoring
docker compose -f docker-compose.yml -f docker-compose.prod.yml --profile monitoring up -d

# tear down (preserves volumes)
docker compose down

# nuclear (DROPS DATA — verify before running)
docker compose down -v
```

## 3. First-time setup

```bash
cp .env.example .env
python -c "import secrets; print(secrets.token_urlsafe(48))"  # paste into APP_SECRET_KEY
docker compose up -d
docker compose exec api alembic upgrade head
docker compose exec api python scripts/seed_all.py
docker compose exec api python scripts/create_api_client.py adtrue \
    --permissions read:master --rate-limit 1000
# Save the printed X-API-Key in your secret manager — it's not recoverable.
```

## 4. Common tasks

### Trigger an ingestion sweep

```bash
TOKEN="$(grep ^APP_SECRET_KEY .env | cut -d= -f2-)"
curl -s -X POST http://localhost:8000/api/v1/admin/ingestion-jobs \
  -H "X-Admin-Token: $TOKEN" -H 'Content-Type: application/json' \
  -d '{"source_code":"osm_overpass","job_type":"area_sweep",
       "params":{"lat":21.0285,"lng":105.8542,"radius_m":1000,"category":"cafe"}}'
```

### Force a dedupe pass

```bash
curl -s -X POST http://localhost:8000/api/v1/admin/dedupe/run -H "X-Admin-Token: $TOKEN"
```

### Manual merge / reject from the UI

Open <http://localhost:8501> → **Dedupe Queue** → adjust eps → expand a
cluster → tick rows → **Merge selected** or **Reject selected**.

### Check the LLM cache

```bash
docker compose exec redis redis-cli --scan --pattern 'poi-lake:dedupe:llm:*' | head
docker compose exec redis redis-cli get poi-lake:dedupe:llm:<id-lo>:<id-hi>
docker compose exec redis redis-cli del poi-lake:dedupe:llm:<id-lo>:<id-hi>  # force re-resolution
```

### Backups

```bash
# manual
./scripts/backup_postgres.sh

# with S3 upload
S3_BUCKET=my-poi-lake-backups ./scripts/backup_postgres.sh

# cron (host)
0 3 * * *  cd /opt/poi-lake && S3_BUCKET=... ./scripts/backup_postgres.sh >> /var/log/poi-backup.log 2>&1
```

### Restore from backup

```bash
./scripts/restore_postgres.sh ./backups/poi_lake_20260101T000000Z.dump
```

## 5. Health & metrics

```bash
curl http://localhost:8000/health             # liveness
curl http://localhost:8000/health/ready       # checks DB + extensions
curl http://localhost:8000/metrics            # Prometheus exposition
curl http://localhost:9191/                   # worker metrics (dramatiq)
```

Key metrics to alert on:

| Metric | When to alert |
|---|---|
| `poi_lake_http_requests_total{status="5xx"}` | rate > 1/s for 5 min |
| `histogram_quantile(0.95, ...http_request_duration...)` | > 1s for 10 min |
| `poi_lake_ingest_errors_total` | rate > 0 for 5 min |
| `poi_lake_queue_depth{queue="ingest"}` | > 1000 sustained |
| `poi_lake_llm_calls_total{outcome="error"}` | rate > 0.1/s |

## 6. Incident playbooks

### Ingestion job stuck in `running`

1. `docker compose logs --tail 200 worker | grep <job_id>` — find what the
   adapter was doing.
2. If the gosom job UUID isn't in `GET http://localhost:8090/api/v1/jobs`,
   the gosom container was restarted mid-flight. Mark the job failed:
   ```sql
   UPDATE ingestion_jobs SET status='failed',
       error_message='superseded by manual cleanup', completed_at=NOW()
   WHERE id=<id> AND status='running';
   ```
3. Re-submit a fresh job if needed.

### Worker thrashing on `another operation in progress`

asyncpg connections are bound to the event loop that opened them. The
worker is configured with `--threads 1` to avoid this. If you raised
`--threads`, lower it. Scale via `--processes` instead.

### Dedupe creating duplicate masters at the same address

Two cases:
* Composite scored `< 0.85` because the address differs in trivial ways
  (extra comma, missing ward). Lower the auto-merge threshold via
  `DEDUPE_AUTO_MERGE_THRESHOLD=0.80`, or fix the addresses upstream.
* The records were normalized in different passes and the existing master
  wasn't reconsidered. Run a dedupe pass with `only_pending=False` (Phase
  7+ feature) or merge manually from the UI.

### `ANTHROPIC_API_KEY` rotated

Update `.env`, `docker compose restart api worker`. Cached resolutions
in Redis are still valid (they don't depend on the key); only new LLM
calls will use the new key.

### Postgres disk full

```bash
docker compose exec postgres psql -U poi -d poi_lake -c "
  SELECT relname, pg_size_pretty(pg_total_relation_size(relid))
  FROM pg_catalog.pg_statio_user_tables ORDER BY pg_total_relation_size(relid) DESC LIMIT 10;
"
```
Likely culprit: `raw_pois` (bronze layer, append-only by design). Archive
old rows older than 1 year:
```sql
INSERT INTO raw_pois_archive SELECT * FROM raw_pois WHERE fetched_at < NOW() - INTERVAL '1 year';
DELETE FROM raw_pois WHERE fetched_at < NOW() - INTERVAL '1 year';
VACUUM FULL raw_pois;
```

## 7. Tuning knobs

All in `.env` unless noted:

| Knob | Default | When to change |
|---|---|---|
| `DEDUPE_AUTO_MERGE_THRESHOLD` | 0.85 | lower if you see real duplicates not merging; raise if too many false merges |
| `DEDUPE_LLM_THRESHOLD` | 0.65 | controls how aggressive the LLM resolver is; higher = fewer LLM calls |
| `DEDUPE_CLUSTER_EPS_METERS` | 55 | raise if you have sparser data; lower in dense urban areas |
| `DEDUPE_SCHEDULE_MINUTES` | 15 | dedupe pass frequency |
| `DATABASE_POOL_SIZE` | 20 | raise on high-traffic api containers |
| dramatiq `--processes` (compose) | 2 | scale workers; each is a single-thread asyncio loop |

## 8. CI

`.github/workflows/ci.yml` runs on every push/PR touching `oohx-poi/`:

1. `ruff check src tests`
2. `mypy --no-incremental src` (informational)
3. `pytest` against ephemeral postgres + redis service containers
4. `docker build` of the api, admin, and postgres images (cached via GHA cache)

The build job depends on lint + tests passing.
