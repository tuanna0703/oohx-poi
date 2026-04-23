# poi-lake

Multi-source POI data lake for the oohx / AdTRUE / TapON stack. Ingests POIs
from Google Places, OSM Overpass, gosom scraper, Vietmap, Foody; runs an AI
dedup + normalization pipeline; and exposes curated master records via REST.

Full specification: [poi-lake-CLAUDE.md](poi-lake-CLAUDE.md).

---

## Phase 1 — Foundation

This commit includes:

- Project skeleton + `pyproject.toml` (Python 3.12, FastAPI 0.115, SQLAlchemy 2.0 async, Alembic, Dramatiq, pgvector, sentence-transformers, Anthropic SDK)
- `docker-compose.yml` with 5 services: `postgres` (PostGIS 3.4 + pgvector 0.7), `redis`, `gosom-scraper`, `api`, `admin` (Streamlit placeholder)
- Multi-stage non-root `Dockerfile`s for `api` and `admin`; custom Postgres image builds pgvector from source
- Alembic set up with one migration ([`20260423_0001_initial_schema.py`](migrations/versions/20260423_0001_initial_schema.py)) covering bronze / silver / gold / reference tables
- Seed data + idempotent runners for `sources` (5 rows, disabled by default), `openooh_categories` (18 top-level + 60+ subs), `brands` (~60 VN brands with regex patterns)
- `GET /health` (liveness) and `GET /health/ready` (DB + extensions check)
- `scripts/health_check.py` for CLI health diagnostics

### Schema fixes applied vs. the original spec

| # | Issue | Fix |
|---|---|---|
| 1 | `processed_pois.merged_into` referenced `master_pois(id)` which was declared later (forward FK) | Reordered migration: `master_pois` created before `processed_pois` |
| 2 | `master_poi_history.master_poi_id` had no FK | Added `FK(master_pois.id) ON DELETE RESTRICT` |
| 3 | `source_refs` generated column assumed array without enforcement | Added `CHECK (jsonb_typeof(source_refs) = 'array')` |
| 4 | Several JSONB columns lacked `DEFAULT`s → NULL pollution risk | Added sensible defaults (`'[]'::jsonb`, `'{}'::jsonb`, `'{}'::bigint[]`) |
| 5 | No `updated_at` maintenance | Added shared trigger function `set_updated_at()` + per-table triggers |
| 6 | DBSCAN `eps=0.0005 degrees` varies with latitude | Deferred to pipeline: cluster in meters via projected geometry (see Phase 4); `DEDUPE_CLUSTER_EPS_METERS` kept in `.env` |
| 7 | Streamlit admin coupled with FastAPI in one image | Separate `admin` service in `docker-compose.yml` with its own Dockerfile |

---

## Quickstart

```bash
# 1. Copy .env.example → .env and fill in at least APP_SECRET_KEY (>=32 chars)
cp .env.example .env
python -c "import secrets; print(secrets.token_urlsafe(48))"   # generate APP_SECRET_KEY

# 2. (Optional) generate a Fernet key for source config encryption
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"

# 3. Build + start the stack
docker compose build
docker compose up -d

# 4. Apply migrations
docker compose exec api alembic upgrade head

# 5. Seed reference tables
docker compose exec api python scripts/seed_all.py

# 6. Verify
curl -fsS http://localhost:8000/health          # {"status":"ok","version":"0.1.0"}
curl -fsS http://localhost:8000/health/ready    # checks DB + postgis + pgvector
docker compose exec api python scripts/health_check.py

# 7. Browse
# API docs:   http://localhost:8000/docs
# Admin UI:   http://localhost:8501  (placeholder, expanded in Phase 6)
# gosom UI:   http://localhost:8090
# Postgres:   localhost:5432  (user=poi, pw=poi, db=poi_lake)
```

## Local development (without Docker)

```bash
# Install with uv (https://github.com/astral-sh/uv) or pip
uv venv --python 3.12
uv pip install -e ".[dev,admin,scraping]"

# You'll still need Postgres 16 + PostGIS + pgvector somewhere.
# Easiest: just run the postgres + redis services from docker-compose:
docker compose up -d postgres redis

# Then run the app locally
alembic upgrade head
python scripts/seed_all.py
uvicorn poi_lake.main:app --reload
```

## Project layout

```
poi-lake/
├── pyproject.toml
├── alembic.ini
├── docker-compose.yml
├── docker/
│   ├── api/Dockerfile           # multi-stage, non-root
│   ├── admin/Dockerfile         # Streamlit
│   └── postgres/Dockerfile      # postgis + pgvector
├── migrations/
│   ├── env.py
│   └── versions/20260423_0001_initial_schema.py
├── src/poi_lake/
│   ├── __init__.py
│   ├── config.py                # pydantic-settings
│   ├── main.py                  # FastAPI app + /health
│   ├── db/
│   │   ├── base.py              # async engine/session
│   │   └── models/              # (Phase 2+)
│   ├── seeds/
│   │   ├── sources.py
│   │   ├── openooh_taxonomy.py
│   │   ├── vn_brands.py
│   │   └── runner.py
│   ├── adapters/                # (Phase 2)
│   ├── pipeline/                # (Phase 3–4)
│   ├── workers/                 # (Phase 2+)
│   └── api/                     # (Phase 5)
├── admin/app.py                 # Streamlit placeholder
├── scripts/
│   ├── seed_sources.py
│   ├── seed_all.py
│   └── health_check.py
└── tests/
    └── unit/test_config.py
```

## Acceptance — Phase 1

- [x] `docker compose up -d` brings the full stack up
- [x] `curl localhost:8000/health` returns `{"status":"ok","version":"0.1.0"}`
- [x] `alembic upgrade head` runs without errors
- [x] `psql -U poi poi_lake -c "SELECT code FROM sources"` returns 5 rows
- [x] `psql -U poi poi_lake -c "SELECT COUNT(*) FROM openooh_categories"` returns ≥18
- [x] `psql -U poi poi_lake -c "SELECT COUNT(*) FROM brands"` returns ≥60

## Roadmap

- **Phase 2** — Ingestion adapters (Google Places, OSM, gosom, Vietmap, Foody)
- **Phase 3** — Normalize pipeline (address/phone/category/brand/embedding)
- **Phase 4** — Dedupe pipeline (spatial clustering + LLM resolver + merge)
- **Phase 5** — REST API (`/api/v1/master-pois`, webhooks)
- **Phase 6** — Streamlit admin UI
- **Phase 7** — Production hardening (metrics, backups, CI)
