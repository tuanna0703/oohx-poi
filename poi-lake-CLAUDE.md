# POI Data Lake (`poi-lake`) — Build Specification

> **Purpose**: Independent data lake service that ingests POI data from multiple sources (Google Places, OSM, gosom scraper, Vietmap, Foody, etc.), runs AI-powered dedup + normalization pipeline, and exposes curated master records via REST API to consumer systems (AdTRUE, TapON SSP, oohx.net).

> **Execution model**: This spec is designed for Claude Code. Execute phases sequentially. Each phase is a self-contained session. Commit after each phase.

---

## 1. Project context

### 1.1 What this replaces / augments

- Existing Python POI crawler (Google Places + OSM Overpass, 18 OpenOOH categories, 62 VN brand patterns, 6 scoring dimensions) → migrated into `poi-lake` as the Google/OSM ingestion adapters.
- gosom/google-maps-scraper → wrapped as a Docker sidecar, called via REST for deep data enrichment (reviews, popular_times, images).
- All downstream consumers (AdTRUE ad sales platform, TapON SSP, oohx.net marketplace) read from `poi-lake` API only — no direct source access.

### 1.2 Non-goals

- Not a CRM / lead management tool (those live in AdTRUE).
- Not a map rendering service (oohx.net handles that).
- Not a real-time POI search (it's a curated lake, refreshed on schedule).

### 1.3 Scale targets

- **Initial**: 100k POIs across Vietnam (Hanoi, HCMC, Da Nang major cities first).
- **Year 1**: 1M POIs across all 63 VN provinces.
- **Read throughput**: 100 req/s peak (AdTRUE inventory sync).
- **Write throughput**: 1000 POIs/min ingestion peak.

---

## 2. Tech stack (fixed — do not deviate)

| Layer | Technology | Version |
|---|---|---|
| Language | Python | 3.12+ |
| Web framework | FastAPI | 0.115+ |
| ORM | SQLAlchemy 2.0 | async mode |
| Migrations | Alembic | latest |
| Database | PostgreSQL | 16 |
| Extensions | PostGIS, pgvector | PostGIS 3.4, pgvector 0.7 |
| Queue | Dramatiq | latest (Redis broker) |
| Cache | Redis | 7.2 |
| Embedding | sentence-transformers | `paraphrase-multilingual-MiniLM-L12-v2` |
| Fuzzy match | rapidfuzz | latest |
| LLM | Anthropic Claude API | claude-sonnet-4-6 for normalization, claude-opus-4-7 for ambiguity resolution |
| Container | Docker + docker-compose | latest |
| Admin UI | Streamlit | 1.40+ (internal tool only) |
| Testing | pytest + pytest-asyncio | latest |
| Linting | ruff + mypy | latest |

### 2.1 Why these choices

- **PostgreSQL + PostGIS + pgvector**: single database for geospatial + vector similarity + relational. No need for separate vector DB (Pinecone/Weaviate) at this scale.
- **Dramatiq over Celery**: simpler, less memory, actor-based model fits adapter pattern.
- **Streamlit for admin**: Tony is familiar with Filament but Streamlit is faster to build for internal data ops; AdTRUE Filament admin stays as the customer-facing UI.
- **Claude for LLM tasks**: Anthropic API is Tony's existing vendor; no new vendor onboarding.

---

## 3. Project structure

```
poi-lake/
├── pyproject.toml                    # uv / pip-tools managed
├── docker-compose.yml                # dev stack: postgres, redis, gosom scraper
├── docker-compose.prod.yml           # production override
├── Dockerfile                        # poi-lake service image
├── .env.example
├── alembic.ini
├── migrations/                       # Alembic migrations
├── src/
│   └── poi_lake/
│       ├── __init__.py
│       ├── config.py                 # pydantic settings
│       ├── main.py                   # FastAPI app entrypoint
│       ├── db/
│       │   ├── base.py               # async engine, session
│       │   ├── models/
│       │   │   ├── raw_poi.py        # Bronze layer
│       │   │   ├── processed_poi.py  # Silver layer
│       │   │   ├── master_poi.py     # Gold layer
│       │   │   ├── source.py         # Source registry
│       │   │   └── ingestion_job.py  # Job tracking
│       │   └── types.py              # custom types (Geography, Vector)
│       ├── adapters/                 # Pluggable source adapters
│       │   ├── base.py               # Adapter ABC
│       │   ├── google_places.py
│       │   ├── osm_overpass.py
│       │   ├── gosom_scraper.py
│       │   ├── vietmap.py
│       │   ├── foody.py
│       │   └── registry.py           # adapter discovery
│       ├── pipeline/                 # AI processing
│       │   ├── normalize/
│       │   │   ├── address.py        # VN address normalizer
│       │   │   ├── phone.py          # E.164 phone normalizer
│       │   │   ├── category.py       # OpenOOH taxonomy mapper
│       │   │   └── brand.py          # Brand detector
│       │   ├── embed.py              # Embedding generation
│       │   ├── dedupe/
│       │   │   ├── cluster.py        # PostGIS DBSCAN clustering
│       │   │   ├── similarity.py     # Vector + fuzzy scoring
│       │   │   └── resolver.py       # LLM-based ambiguity resolver
│       │   ├── quality.py            # Quality scoring
│       │   └── merge.py              # Master record builder
│       ├── workers/                  # Dramatiq actors
│       │   ├── ingest.py
│       │   ├── process.py
│       │   ├── dedupe.py
│       │   └── schedule.py           # Periodic tasks
│       ├── api/
│       │   ├── v1/
│       │   │   ├── pois.py           # /pois endpoints
│       │   │   ├── search.py         # /search endpoints
│       │   │   ├── jobs.py           # /jobs endpoints
│       │   │   ├── webhooks.py       # /webhooks endpoints
│       │   │   └── admin.py          # /admin endpoints
│       │   └── deps.py               # dependencies (auth, db session)
│       ├── schemas/                  # Pydantic DTOs
│       ├── services/                 # Business logic
│       └── utils/
├── admin/                            # Streamlit admin UI
│   ├── app.py
│   └── pages/
├── tests/
│   ├── unit/
│   ├── integration/
│   └── fixtures/
├── scripts/
│   ├── seed_sources.py
│   ├── backfill.py
│   └── health_check.py
└── docs/
    ├── architecture.md
    ├── adapters.md
    └── api.md
```

---

## 4. Database schema

### 4.1 Bronze layer (raw data)

```sql
-- Source registry
CREATE TABLE sources (
  id SERIAL PRIMARY KEY,
  code VARCHAR(50) UNIQUE NOT NULL,     -- 'google_places', 'osm', 'gosom', 'vietmap', 'foody'
  name VARCHAR(200) NOT NULL,
  adapter_class VARCHAR(200) NOT NULL,   -- Python class path
  config JSONB DEFAULT '{}'::jsonb,      -- rate limits, API keys (encrypted), etc.
  enabled BOOLEAN DEFAULT true,
  priority INT DEFAULT 100,              -- lower = higher priority in merge
  created_at TIMESTAMPTZ DEFAULT NOW(),
  updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- Ingestion jobs (tracking)
CREATE TABLE ingestion_jobs (
  id BIGSERIAL PRIMARY KEY,
  source_id INT REFERENCES sources(id),
  job_type VARCHAR(50) NOT NULL,         -- 'area_sweep', 'category_search', 'detail_enrich'
  params JSONB NOT NULL,
  status VARCHAR(20) NOT NULL,           -- 'pending', 'running', 'completed', 'failed'
  stats JSONB DEFAULT '{}'::jsonb,       -- {fetched: N, errors: M, new: X, updated: Y}
  started_at TIMESTAMPTZ,
  completed_at TIMESTAMPTZ,
  error_message TEXT,
  created_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX idx_jobs_status ON ingestion_jobs(status) WHERE status IN ('pending', 'running');

-- Raw POIs (bronze)
CREATE TABLE raw_pois (
  id BIGSERIAL PRIMARY KEY,
  source_id INT NOT NULL REFERENCES sources(id),
  source_poi_id VARCHAR(255) NOT NULL,   -- place_id, osm_id, fsq_id...
  raw_payload JSONB NOT NULL,
  location GEOGRAPHY(POINT, 4326),       -- extracted for indexing
  content_hash VARCHAR(64) NOT NULL,     -- SHA256 of normalized payload, for change detection
  fetched_at TIMESTAMPTZ DEFAULT NOW(),
  ingestion_job_id BIGINT REFERENCES ingestion_jobs(id),
  processed_at TIMESTAMPTZ,              -- NULL = not yet processed into silver
  UNIQUE(source_id, source_poi_id, content_hash)
);
CREATE INDEX idx_raw_location ON raw_pois USING GIST(location);
CREATE INDEX idx_raw_payload ON raw_pois USING GIN(raw_payload);
CREATE INDEX idx_raw_unprocessed ON raw_pois(processed_at) WHERE processed_at IS NULL;
CREATE INDEX idx_raw_source_fetched ON raw_pois(source_id, fetched_at DESC);
```

### 4.2 Silver layer (processed + normalized)

```sql
CREATE TABLE processed_pois (
  id BIGSERIAL PRIMARY KEY,
  raw_poi_id BIGINT NOT NULL REFERENCES raw_pois(id),

  -- Normalized fields
  name_original TEXT NOT NULL,
  name_normalized TEXT NOT NULL,         -- lowercase, accent-normalized
  name_embedding VECTOR(384) NOT NULL,   -- multilingual MiniLM-L12

  address_original TEXT,
  address_normalized TEXT,               -- VN-specific normalization
  address_components JSONB,              -- {street, ward, district, city, country}

  phone_original TEXT,
  phone_e164 VARCHAR(20),                -- +84...

  website TEXT,
  website_domain VARCHAR(255),

  -- Classification
  openooh_category VARCHAR(50),          -- v1.1 taxonomy
  openooh_subcategory VARCHAR(100),
  raw_category VARCHAR(100),             -- original from source
  brand VARCHAR(100),                    -- detected: Circle K, GS25, etc.
  brand_confidence NUMERIC(3,2),

  -- Geospatial
  location GEOGRAPHY(POINT, 4326) NOT NULL,

  -- Metadata
  quality_score NUMERIC(3,2),            -- 0-1, composite
  quality_factors JSONB,                 -- {completeness: 0.8, freshness: 0.9, ...}

  -- Status
  merged_into UUID REFERENCES master_pois(id),   -- NULL = unmerged candidate
  merge_status VARCHAR(20) DEFAULT 'pending',    -- 'pending', 'merged', 'duplicate', 'rejected'
  merge_reason TEXT,

  created_at TIMESTAMPTZ DEFAULT NOW(),
  updated_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX idx_proc_location ON processed_pois USING GIST(location);
CREATE INDEX idx_proc_embedding ON processed_pois USING hnsw (name_embedding vector_cosine_ops);
CREATE INDEX idx_proc_unmerged ON processed_pois(merge_status) WHERE merge_status = 'pending';
CREATE INDEX idx_proc_brand ON processed_pois(brand) WHERE brand IS NOT NULL;
CREATE INDEX idx_proc_category ON processed_pois(openooh_category, openooh_subcategory);
```

### 4.3 Gold layer (master records)

```sql
CREATE TABLE master_pois (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),

  -- Canonical fields (picked from best source)
  canonical_name TEXT NOT NULL,
  canonical_name_embedding VECTOR(384) NOT NULL,
  canonical_address TEXT,
  canonical_address_components JSONB,
  canonical_phone VARCHAR(20),
  canonical_website TEXT,

  location GEOGRAPHY(POINT, 4326) NOT NULL,

  openooh_category VARCHAR(50),
  openooh_subcategory VARCHAR(100),
  brand VARCHAR(100),

  -- Aggregated metadata
  source_refs JSONB NOT NULL,            -- [{source: 'google', source_poi_id: '...', raw_poi_id: N}, ...]
  merged_processed_ids BIGINT[] NOT NULL,
  sources_count INT GENERATED ALWAYS AS (jsonb_array_length(source_refs)) STORED,

  -- Scoring
  confidence NUMERIC(3,2) NOT NULL,      -- master record confidence
  quality_score NUMERIC(3,2),

  -- Business fields (for DOOH use case)
  dooh_score NUMERIC(3,2),               -- 0-1 suitability for DOOH placement
  dooh_score_factors JSONB,              -- breakdown of 6 scoring dimensions

  -- Lifecycle
  status VARCHAR(20) DEFAULT 'active',   -- 'active', 'archived', 'merged_away'
  archived_reason TEXT,
  merged_into UUID REFERENCES master_pois(id),  -- if this record got merged into another

  version INT DEFAULT 1,                 -- bumped on updates
  created_at TIMESTAMPTZ DEFAULT NOW(),
  updated_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX idx_master_location ON master_pois USING GIST(location);
CREATE INDEX idx_master_embedding ON master_pois USING hnsw (canonical_name_embedding vector_cosine_ops);
CREATE INDEX idx_master_brand ON master_pois(brand) WHERE brand IS NOT NULL;
CREATE INDEX idx_master_category ON master_pois(openooh_category);
CREATE INDEX idx_master_active ON master_pois(status, updated_at DESC) WHERE status = 'active';

-- Audit log for master record changes
CREATE TABLE master_poi_history (
  id BIGSERIAL PRIMARY KEY,
  master_poi_id UUID NOT NULL,
  version INT NOT NULL,
  changed_fields JSONB NOT NULL,
  previous_values JSONB NOT NULL,
  new_values JSONB NOT NULL,
  change_reason VARCHAR(100),            -- 'new_source_added', 'llm_resolved', 'manual_edit', etc.
  changed_at TIMESTAMPTZ DEFAULT NOW()
);
```

### 4.4 Supporting tables

```sql
-- Known brands (reference table, seeded)
CREATE TABLE brands (
  id SERIAL PRIMARY KEY,
  name VARCHAR(100) UNIQUE NOT NULL,
  aliases TEXT[],                        -- ['Circle K', 'CircleK', 'circlek']
  category VARCHAR(50),                  -- openooh category
  parent_company VARCHAR(200),
  country VARCHAR(2),
  match_pattern TEXT,                    -- regex
  enabled BOOLEAN DEFAULT true
);

-- OpenOOH taxonomy (seeded from v1.1 spec)
CREATE TABLE openooh_categories (
  code VARCHAR(50) PRIMARY KEY,
  name VARCHAR(200) NOT NULL,
  parent_code VARCHAR(50) REFERENCES openooh_categories(code),
  level INT NOT NULL
);

-- API consumers (for access control)
CREATE TABLE api_clients (
  id SERIAL PRIMARY KEY,
  name VARCHAR(200) NOT NULL,            -- 'adtrue', 'tapon-ssp', 'oohx'
  api_key_hash VARCHAR(64) UNIQUE NOT NULL,
  permissions JSONB DEFAULT '[]'::jsonb, -- ['read:master', 'read:raw', 'write:webhook']
  rate_limit_per_minute INT DEFAULT 1000,
  enabled BOOLEAN DEFAULT true,
  created_at TIMESTAMPTZ DEFAULT NOW()
);
```

---

## 5. Adapter interface

All source adapters implement this contract:

```python
# src/poi_lake/adapters/base.py

from abc import ABC, abstractmethod
from typing import AsyncIterator
from pydantic import BaseModel

class RawPOIRecord(BaseModel):
    source_poi_id: str
    raw_payload: dict
    location: tuple[float, float] | None   # (lat, lng)

class AdapterConfig(BaseModel):
    api_key: str | None = None
    rate_limit_per_second: float = 1.0
    timeout_seconds: int = 30
    extra: dict = {}

class SourceAdapter(ABC):
    """Base class for all POI source adapters."""

    code: str                              # unique identifier, matches sources.code
    name: str

    def __init__(self, config: AdapterConfig):
        self.config = config

    @abstractmethod
    async def fetch_by_area(
        self,
        lat: float,
        lng: float,
        radius_m: int,
        category: str | None = None,
    ) -> AsyncIterator[RawPOIRecord]:
        """Fetch POIs within a circular area."""
        ...

    @abstractmethod
    async def fetch_by_id(self, source_poi_id: str) -> RawPOIRecord | None:
        """Fetch a specific POI by its source ID (for detail enrichment)."""
        ...

    @abstractmethod
    async def health_check(self) -> bool:
        """Verify the adapter can connect to its source."""
        ...
```

Adapters to implement in Phase 2:

| Adapter | Source | Auth | Rate limit | Notes |
|---|---|---|---|---|
| `GooglePlacesAdapter` | Google Places API (New) | API key | 100 QPS | Use v1 endpoint, field masks to minimize cost |
| `OSMOverpassAdapter` | OSM Overpass API | None | Self-hosted instance recommended | Use `https://overpass-api.de` for dev, self-host for prod |
| `GosomScraperAdapter` | gosom docker container | Internal | 120 places/min | Calls REST API of gosom sidecar |
| `VietmapAdapter` | Vietmap Maps API | API key | TBD | `https://maps.vietmap.vn/api` |
| `FoodyAdapter` | Foody.vn scraping | None (scraping) | 2 req/s | Playwright-based, respect robots.txt |

---

## 6. AI processing pipeline

### 6.1 Normalize stage (per-record, stateless)

For each new `raw_poi`, run:

1. **Extract** canonical fields from `raw_payload` (adapter-specific, each adapter provides an extractor).
2. **Normalize name**: lowercase, unaccent (`unidecode`), strip branding suffixes.
3. **Normalize address** (VN-specific):
   - Parse into components using `libpostal` bindings or regex rules.
   - Standardize ward/district prefixes (`P.` → `Phường`, `Q.` → `Quận`).
   - Use LLM fallback for ambiguous addresses (Claude Sonnet, prompt: "Parse this Vietnamese address into components: ...").
4. **Normalize phone**: parse with `phonenumbers` library, format as E.164.
5. **Detect brand**: match against `brands` table using aliases + regex.
6. **Map category**: rule-based mapping source_category → OpenOOH taxonomy. LLM fallback for unmatched.
7. **Generate embedding**: `name_normalized` → 384-dim vector via sentence-transformers.
8. **Compute quality score** based on 6 factors:
   - `completeness`: fraction of non-null canonical fields
   - `freshness`: decay function of `fetched_at`
   - `source_reliability`: static per-source score
   - `address_confidence`: parser confidence
   - `phone_valid`: boolean → 0/1
   - `has_coordinates`: boolean → 0/1

Output: `processed_poi` record with `merge_status='pending'`.

### 6.2 Dedupe stage (batch, stateful)

Runs periodically (every 15 min) on unmerged processed_pois:

**Step 1: Spatial clustering**
```sql
SELECT id, ST_ClusterDBSCAN(location::geometry, eps := 0.0005, minpoints := 1)
  OVER () AS cluster_id
FROM processed_pois
WHERE merge_status = 'pending';
```
`eps=0.0005` degrees ≈ 55m at equator. Adjusts cluster radius per latitude.

**Step 2: Within each cluster, pairwise scoring**

For each pair in cluster:
```python
score = (
    0.40 * cosine_similarity(a.name_embedding, b.name_embedding) +
    0.25 * fuzz.token_set_ratio(a.address_normalized, b.address_normalized) / 100 +
    0.15 * (1.0 if a.phone_e164 == b.phone_e164 and a.phone_e164 else 0.5) +
    0.10 * (1.0 if a.website_domain == b.website_domain and a.website_domain else 0.5) +
    0.10 * (1.0 if a.brand == b.brand and a.brand else 0.5)
)
```

**Step 3: Decision**
- `score >= 0.85`: auto-merge
- `0.65 <= score < 0.85`: send to LLM resolver
- `score < 0.65`: treat as distinct

**Step 4: LLM resolver (ambiguous cases)**

Prompt Claude Opus 4.7 with both records as JSON, ask:
```
Are these two records referring to the same physical location?
Answer with JSON: {"same": true|false, "confidence": 0-1, "reason": "..."}
```
Cache result by `(record_a_id, record_b_id)` hash for 7 days.

### 6.3 Merge stage

For each merge decision:
1. Pick canonical values per field using priority:
   - Highest `quality_score` wins
   - Tie-break: highest `sources.priority` (= lowest numerical value)
   - For `location`: weighted centroid by quality_score
2. Create or update `master_poi` record.
3. Update `processed_pois.merged_into = master_id`, `merge_status = 'merged'`.
4. Write audit log to `master_poi_history`.

---

## 7. REST API design

All endpoints under `/api/v1`. Auth via `X-API-Key` header.

### 7.1 Master POI endpoints (read-only for consumers)

```
GET  /api/v1/master-pois
  ?lat=21.0285&lng=105.8542&radius_m=5000
  &category=retail
  &brand=Circle%20K
  &min_confidence=0.8
  &page=1&per_page=100

GET  /api/v1/master-pois/{id}
GET  /api/v1/master-pois/{id}/sources          # lineage: which raw_pois merged
GET  /api/v1/master-pois/{id}/history          # audit log

POST /api/v1/master-pois/search
  Body: {
    "query": "Circle K Lang Ha",               # fuzzy text search
    "bbox": [lng_min, lat_min, lng_max, lat_max],
    "filters": {...}
  }
```

### 7.2 Admin endpoints (internal only)

```
POST /api/v1/admin/ingestion-jobs              # trigger new ingestion
GET  /api/v1/admin/ingestion-jobs              # list jobs
GET  /api/v1/admin/sources                     # source registry
POST /api/v1/admin/sources/{id}/disable
POST /api/v1/admin/pipeline/reprocess          # force reprocess a POI
POST /api/v1/admin/dedupe/run                  # manually trigger dedup
```

### 7.3 Webhooks (outgoing to consumers)

When a master_poi is created/updated/merged, POST to registered webhooks:
```json
{
  "event": "master_poi.updated",
  "timestamp": "2026-04-23T10:00:00Z",
  "data": { "master_poi": {...}, "changes": [...] }
}
```

---

## 8. Implementation phases

Execute one phase per Claude Code session. Commit after each.

### Phase 1: Foundation (1 session)

**Goal**: Project skeleton + DB schema + Docker dev stack

- [ ] Initialize Python project with `uv` or `pdm`, set up `pyproject.toml` with all dependencies pinned.
- [ ] Write `docker-compose.yml` with services: `postgres` (with postgis+pgvector), `redis`, `gosom-scraper`, `poi-lake-api`.
- [ ] Write Dockerfile for `poi-lake` service (multi-stage build, non-root user).
- [ ] Configure `alembic` for migrations.
- [ ] Create initial migration with all schemas from Section 4.
- [ ] Seed `sources` table with 5 adapter configs (disabled by default).
- [ ] Seed `openooh_categories` table from OpenOOH v1.1 taxonomy.
- [ ] Seed `brands` table with 62 VN brand patterns (migrate from existing POI crawler).
- [ ] Write `src/poi_lake/config.py` with pydantic Settings, `.env.example`.
- [ ] Write FastAPI `main.py` with health check endpoint.
- [ ] Write `scripts/seed_sources.py` and `scripts/health_check.py`.
- [ ] Write basic `README.md` with setup instructions.
- [ ] Verify: `docker-compose up` brings everything up green, `/health` returns 200.

**Acceptance criteria**:
- `docker-compose up -d && curl localhost:8000/health` returns `{"status": "ok"}`.
- `alembic upgrade head` runs without errors.
- `psql` can query `SELECT * FROM sources` and see 5 rows.

### Phase 2: Ingestion adapters (2 sessions)

**Session 2a**: Adapter framework + Google Places + OSM

- [ ] Implement `SourceAdapter` ABC (Section 5).
- [ ] Implement `GooglePlacesAdapter` (migrate from existing Python crawler).
- [ ] Implement `OSMOverpassAdapter` (migrate from existing Python crawler).
- [ ] Implement adapter registry with entry-point discovery.
- [ ] Implement `IngestionService` that orchestrates adapter calls → raw_pois inserts.
- [ ] Implement content-hash dedup on insert (skip if `(source_id, source_poi_id, content_hash)` exists).
- [ ] Write Dramatiq worker for async ingestion jobs.
- [ ] Write pytest fixtures with mocked API responses.
- [ ] Write integration test: ingest 10 POIs from mocked Google Places, verify DB state.

**Session 2b**: gosom + Vietmap + Foody adapters

- [ ] Implement `GosomScraperAdapter` (wraps gosom docker REST API from Section 6 of [gosom README](https://github.com/gosom/google-maps-scraper)).
- [ ] Implement `VietmapAdapter` (Vietmap Maps API — register account first to get API key).
- [ ] Implement `FoodyAdapter` (Playwright-based scraper; handle rate limiting; respect robots.txt).
- [ ] Write per-adapter tests.

**Acceptance criteria**:
- Trigger a job via `POST /api/v1/admin/ingestion-jobs` for each adapter → rows appear in `raw_pois`.
- Idempotency: running same job twice doesn't duplicate rows.

### Phase 3: Normalize pipeline (1 session)

- [ ] Implement `AddressNormalizer` for VN addresses (rule-based + LLM fallback).
- [ ] Implement `PhoneNormalizer` using `phonenumbers` lib.
- [ ] Implement `CategoryMapper` for OpenOOH taxonomy.
- [ ] Implement `BrandDetector` using `brands` table.
- [ ] Implement `EmbeddingService` using sentence-transformers (cache model in volume).
- [ ] Implement `QualityScorer` with 6-factor scoring.
- [ ] Implement `NormalizePipeline` that takes raw_poi_id → writes processed_poi.
- [ ] Wire as Dramatiq worker triggered on raw_poi insert.
- [ ] Write tests for each normalizer (VN-specific cases from existing crawler's test data).

**Acceptance criteria**:
- After Phase 2 ingestion, running normalize worker populates `processed_pois` table.
- All `processed_pois` rows have non-null `name_embedding`, `quality_score`.

### Phase 4: Dedupe pipeline (2 sessions)

**Session 4a**: Clustering + similarity

- [ ] Implement `SpatialClusterer` using PostGIS ST_ClusterDBSCAN.
- [ ] Implement `PairSimilarityScorer` with 5-component weighted score.
- [ ] Implement decision logic (auto-merge / LLM / distinct).
- [ ] Tests with synthetic clusters (3 Circle K locations with slight variations).

**Session 4b**: LLM resolver + merge logic

- [ ] Implement `LLMResolver` using Anthropic Claude Opus 4.7.
- [ ] Implement resolver cache (Redis) with 7-day TTL.
- [ ] Implement `MasterRecordBuilder` that picks canonical values.
- [ ] Implement `MergeService` with transaction + audit log.
- [ ] Wire as Dramatiq worker on schedule (every 15 min).
- [ ] Tests: verify master_pois get created with correct lineage.

**Acceptance criteria**:
- After Phase 3, running dedupe worker produces master_pois rows with proper `source_refs` and `merged_processed_ids`.
- Auditable: every change logged in `master_poi_history`.

### Phase 5: REST API (1 session)

- [ ] Implement API key auth middleware.
- [ ] Implement rate limiting per `api_clients` config (use `slowapi`).
- [ ] Implement all endpoints from Section 7.
- [ ] Implement webhook dispatch (Dramatiq worker + retry with exponential backoff).
- [ ] OpenAPI spec auto-generation (FastAPI default).
- [ ] Integration tests covering auth, pagination, filtering, webhooks.

**Acceptance criteria**:
- `/docs` shows full OpenAPI spec.
- `curl -H "X-API-Key: xxx" localhost:8000/api/v1/master-pois?lat=21.02&lng=105.85&radius_m=1000` returns JSON with POIs.

### Phase 6: Admin UI (1 session)

- [ ] Streamlit app with pages: Dashboard, Sources, Ingestion Jobs, POI Explorer, Dedupe Queue, Audit Log.
- [ ] Dashboard: KPIs (total POIs per layer, jobs status, last 24h ingestion rate).
- [ ] POI Explorer: map view using `streamlit-folium`, filter by category/brand/confidence.
- [ ] Dedupe Queue: review ambiguous LLM decisions, allow manual override.
- [ ] Audit Log: search master_poi_history.

**Acceptance criteria**:
- `streamlit run admin/app.py` serves dashboard.
- Can manually override a merge decision from the UI.

### Phase 7: Production hardening (1 session)

- [ ] Prometheus metrics (requests, ingestion rate, queue depth, LLM cost per day).
- [ ] Structured logging with `structlog` + JSON format.
- [ ] Grafana dashboard config.
- [ ] Production `docker-compose.prod.yml` with:
  - External volumes for postgres data
  - Resource limits
  - Health checks
  - Restart policies
- [ ] Backup script for postgres (pg_dump to S3-compatible storage).
- [ ] CI pipeline (GitHub Actions): ruff, mypy, pytest, Docker build.
- [ ] Write `docs/runbook.md` with common operational scenarios.

---

## 9. Configuration (`.env.example`)

```ini
# App
APP_ENV=development
APP_HOST=0.0.0.0
APP_PORT=8000
APP_SECRET_KEY=change-me-in-prod

# Database
DATABASE_URL=postgresql+asyncpg://poi:poi@postgres:5432/poi_lake
DATABASE_POOL_SIZE=20

# Redis
REDIS_URL=redis://redis:6379/0

# Anthropic
ANTHROPIC_API_KEY=sk-ant-...
ANTHROPIC_MODEL_NORMALIZE=claude-sonnet-4-6
ANTHROPIC_MODEL_RESOLVER=claude-opus-4-7

# Adapters
GOOGLE_PLACES_API_KEY=
VIETMAP_API_KEY=
OSM_OVERPASS_URL=https://overpass-api.de/api/interpreter
GOSOM_SCRAPER_URL=http://gosom-scraper:8080

# Pipeline
DEDUPE_CLUSTER_EPS_METERS=55
DEDUPE_AUTO_MERGE_THRESHOLD=0.85
DEDUPE_LLM_THRESHOLD=0.65
DEDUPE_SCHEDULE_MINUTES=15

# Embedding
EMBEDDING_MODEL=paraphrase-multilingual-MiniLM-L12-v2
EMBEDDING_CACHE_DIR=/app/models

# Webhooks
WEBHOOK_TIMEOUT_SECONDS=10
WEBHOOK_MAX_RETRIES=5
```

---

## 10. Operational notes

### 10.1 Cost estimates (LLM)

- **Normalize stage** (Claude Sonnet): only ~5% of records hit LLM fallback (ambiguous addresses). At 1M POIs, ~50k LLM calls, ~200 tokens each → ~$50 one-time.
- **Dedupe resolver** (Claude Opus): ~2-5% of pairs go to LLM. Assume 100k pairs/year → $500-1000/year.
- **Total LLM**: < $100/month at steady state.

### 10.2 Rate limit strategy

- Google Places: 100 QPS default, but cost matters. Use field masks aggressively. Cache results 30 days.
- Vietmap: check quota at registration.
- Foody: conservative 2 req/s, rotate user agents, respect `Retry-After`.
- OSM Overpass: self-host for prod (single-server Docker image exists); public instance for dev only.

### 10.3 Data lineage

Every master_poi knows its lineage via `source_refs` and `merged_processed_ids`. Never delete raw_pois — they're the source of truth. Instead, use `status` columns for soft-archive.

### 10.4 Security

- All API keys encrypted at rest in `sources.config` (use `cryptography.fernet`, master key from env).
- `api_clients.api_key_hash` stored as SHA256, never plaintext.
- All webhook URLs validated against allowlist domains per client.
- No PII in logs (addresses are fine, but no names if ingested).

---

## 11. Migration from existing POI crawler

The existing Python POI crawler (Google Places + OSM, 18 OpenOOH categories, 62 brand patterns, 6 scoring dimensions) maps into this project as:

| Existing file | Migrated to |
|---|---|
| `google_places_fetcher.py` | `src/poi_lake/adapters/google_places.py` |
| `osm_overpass_fetcher.py` | `src/poi_lake/adapters/osm_overpass.py` |
| `openooh_taxonomy.py` | Seed data for `openooh_categories` table |
| `vn_brands.py` | Seed data for `brands` table |
| `scoring.py` (6 dimensions) | `src/poi_lake/pipeline/quality.py` + `dooh_score` column |
| `sector_recommender.py` | **Stays in AdTRUE/TapON side** — consumes master_pois via API |

---

## 12. Success metrics

Measure these weekly once in production:

- **Coverage**: # master_pois with ≥2 sources / total master_pois (target: > 40%)
- **Dedup accuracy**: sampled manual review, target > 95% correct merges
- **Freshness**: median age of source records per master_poi (target: < 30 days)
- **Query latency**: p95 for `GET /master-pois?radius=5000` (target: < 200ms)
- **LLM cost**: $/1000 new POIs processed (target: < $0.10)

---

## 13. Out of scope (future phases)

- Multi-tenant (each AdTRUE tenant sees different POIs) — currently single-tenant
- POI ownership claims (users claiming their business) — AdTRUE handles this
- Real-time streaming updates — current model is batch (15-min dedup cycle)
- ML-based category classification — rule-based + LLM fallback for now
- Image similarity for POI photos — text only for v1
- Support for non-VN countries — codebase is language-agnostic but seeds are VN-specific

---

**End of specification.**

Execute Phase 1. When complete, commit with message `feat: phase 1 - foundation` and wait for next instruction.
