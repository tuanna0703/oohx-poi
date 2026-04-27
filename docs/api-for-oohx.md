# POI Lake API — Consumer Guide for oohx

This is the integration guide for systems calling poi-lake from the
outside (oohx.net, AdTRUE, TapON). It covers auth, the endpoints you'll
actually use, and the gotchas worth knowing up front.

## Base URL

```
https://api.poi.oohx.net/api/v1
```

Interactive OpenAPI/Swagger:

```
https://api.poi.oohx.net/docs
```

The full machine-readable schema is at
`https://api.poi.oohx.net/openapi.json` — you can codegen a client from
it (see §6).

## Authentication

Every request needs an `X-API-Key` header. Keys are issued by ops with
`scripts/create_api_client.py`. They are shown **once** when created;
the server only stores a SHA-256 hash, so a lost key must be re-issued.

```bash
curl -s https://api.poi.oohx.net/api/v1/master-pois \
  -H "X-API-Key: $POI_LAKE_API_KEY" \
  -G --data-urlencode 'lat=21.0285' \
     --data-urlencode 'lng=105.8542' \
     --data-urlencode 'radius_m=1000'
```

| Status | Meaning |
|---|---|
| 401 | missing / unknown key |
| 403 | key is disabled, or lacks the permission for this endpoint |
| 429 | per-minute rate limit exceeded — see `Retry-After` header |

## Rate limiting

Each client has a `rate_limit_per_minute` (default 1000). Every response
carries:

```
X-RateLimit-Limit: 1000
X-RateLimit-Remaining: 943
X-RateLimit-Reset: 1714123920    # epoch seconds when window flips
```

Handling 429 cleanly:

```python
import time
import httpx

def get(client: httpx.Client, url: str, params: dict | None = None) -> dict:
    for attempt in range(3):
        r = client.get(url, params=params)
        if r.status_code != 429:
            r.raise_for_status()
            return r.json()
        time.sleep(int(r.headers.get("Retry-After", 1)))
    r.raise_for_status()
```

## Endpoints

All endpoints below require the `read:master` permission.

### `GET /master-pois`

List active master POIs with filters. Use this for "give me X within Y
metres" map queries.

| Query | Type | Notes |
|---|---|---|
| `lat`, `lng`, `radius_m` | float, float, int | provide all three together; metres |
| `category` | string | matches OpenOOH `category` **or** `subcategory` |
| `brand` | string | exact match (case-sensitive) |
| `province_code` | string | e.g. `01` (Hà Nội), `79` (HCM) |
| `district_code` | string | e.g. `01.005` (Cầu Giấy) |
| `ward_code` | string | (sparsely populated for now) |
| `min_confidence` | float 0–1 | default 0.0 |
| `page`, `per_page` | int | default 1 / 50; max per_page=200 |

**Example** — every Starbucks within 5 km of Hồ Hoàn Kiếm:

```bash
curl -G https://api.poi.oohx.net/api/v1/master-pois \
  -H "X-API-Key: $KEY" \
  --data-urlencode 'lat=21.0285' \
  --data-urlencode 'lng=105.8542' \
  --data-urlencode 'radius_m=5000' \
  --data-urlencode 'brand=Starbucks'
```

**Response shape**

```json
{
  "items": [
    {
      "id": "0bc6f...",
      "canonical_name": "Starbucks Coffee Tràng Tiền Plaza",
      "canonical_address": "24 Tràng Tiền, Hoàn Kiếm, Hà Nội",
      "canonical_phone": "+842439330050",
      "canonical_website": "https://starbucks.vn",
      "lat": 21.024873,
      "lng": 105.853562,
      "openooh_category": "retail",
      "openooh_subcategory": "retail.cafes",
      "brand": "Starbucks",
      "province_code": "01",
      "district_code": "01.002",
      "ward_code": null,
      "sources_count": 3,
      "confidence": 0.92,
      "quality_score": 0.81,
      "dooh_score": null,
      "status": "active",
      "version": 4,
      "created_at": "2026-04-22T05:13:00Z",
      "updated_at": "2026-04-26T09:01:00Z"
    }
  ],
  "total": 28,
  "page": 1,
  "per_page": 50
}
```

### `GET /master-pois/{id}`

Single record by UUID.

### `GET /master-pois/{id}/sources`

Lineage — which raw_pois were merged into this master, with the source
they came from. Use this when a customer asks "where did this data
come from?" and you need to point at provenance.

```json
[
  { "source": "google_places", "source_poi_id": "ChIJ...", "raw_poi_id": 8421 },
  { "source": "osm_overpass", "source_poi_id": "n472103", "raw_poi_id": 8543 }
]
```

### `GET /master-pois/{id}/history`

Versioned audit log. Each row has the changed fields, previous values,
new values, and a `change_reason` (`auto_merge`, `manual_merge`,
`canonical_field_recompute`, `manual_edit`, etc.).

### `POST /master-pois/search`

Fuzzy text + bbox query. Body:

```json
{
  "query": "phở thìn lò đúc",
  "bbox": [105.84, 21.00, 105.88, 21.04],
  "category": null,
  "brand": null,
  "min_confidence": 0.6,
  "page": 1,
  "per_page": 50
}
```

`query` is split on whitespace and matched as `ILIKE` substrings against
`canonical_name` and `canonical_address` (cap 5 tokens). Use this when
you have a user-typed string; use the GET form for filter-driven map
queries.

### `GET /master-pois/brands`

Aggregated brand counts. Use for "show me all chains in Hà Nội"
inventory views.

```bash
curl -G https://api.poi.oohx.net/api/v1/master-pois/brands \
  -H "X-API-Key: $KEY" \
  --data-urlencode 'province_code=01' \
  --data-urlencode 'limit=50'
```

```json
[
  { "brand": "Highlands Coffee", "count": 142, "category": "retail.cafes", "sample_master_ids": ["..."] },
  { "brand": "Vincom Plaza", "count": 21, "category": "retail.shopping_centers", "sample_master_ids": ["..."] }
]
```

## Data model notes

* **`master_pois`** is the curated, deduplicated layer. There is at most
  one row per real-world place. Don't query the bronze (`raw_pois`) or
  silver (`processed_pois`) layers from outside — they contain
  pre-merge duplicates and shape changes between releases.
* **`confidence`** in `[0, 1]` is the merge engine's certainty that this
  cluster represents one real place. Values < 0.6 are usually
  single-source records; values > 0.9 have ≥3 corroborating sources.
* **`dooh_score`** in `[0, 1]` (when present) is the DOOH-suitability
  score: foot traffic + visibility + proximity to inventory. Currently
  populated for retail/hospitality categories only.
* **OpenOOH taxonomy** — `openooh_category` is the level-1 code (e.g.
  `retail`), `openooh_subcategory` is level-2 (e.g. `retail.cafes`).
  Reference: https://github.com/openooh/venue-taxonomy
* **Vietnamese admin codes** — provinces are 2-digit (`01` = Hà Nội),
  districts are `<province>.<NNN>` (`01.005` = Cầu Giấy). District
  coverage is currently HN/HCM/ĐN; other provinces resolve to province
  level only.

## Caching guidance

Master POIs change slowly — once dedupe stabilises a record, it
typically gets one update per quarter. Reasonable cache TTLs:

| Endpoint | TTL |
|---|---|
| `GET /master-pois` (filter query) | 5 min |
| `GET /master-pois/{id}` | 1 hour |
| `GET /master-pois/{id}/sources` | 1 day |
| `GET /master-pois/brands` | 15 min |

Use `updated_at` from the response as a cache-busting key. There is no
ETag/If-Modified-Since support yet (planned for Phase 9).

## Errors

All 4xx/5xx return:

```json
{ "detail": "human-readable explanation" }
```

Common cases:

| Status | When | Action |
|---|---|---|
| 400 | bad query (e.g. `lat` without `lng`) | fix request |
| 401 | missing / bad key | check `X-API-Key` |
| 403 | client disabled or lacks permission | contact ops |
| 404 | master id doesn't exist | likely deleted/superseded — query history |
| 422 | request body validation failed | see `detail` for the field |
| 429 | rate limit hit | back off per `Retry-After` |
| 5xx | server-side bug or DB outage | retry with exponential backoff; report if persists |

## Webhooks (planned)

Phase 5b adds outbound webhooks on `master.created`, `master.updated`,
`master.merged`. If you'd rather pull, the `updated_at` filter on
`GET /master-pois` is the recommended sync pattern in the meantime:

```python
since = last_sync_iso  # store between runs
poll(f"/master-pois?per_page=200&min_confidence=0.7", since=since)
```

(There's no `since` query param yet — Phase 9. For now, page through
all results and dedupe client-side by `id`.)

## Reference: full schema

See `https://api.poi.oohx.net/openapi.json`. To regenerate a typed
Python client:

```bash
pip install openapi-python-client
openapi-python-client generate \
  --url https://api.poi.oohx.net/openapi.json \
  --meta none
```

For TypeScript:

```bash
npx openapi-typescript https://api.poi.oohx.net/openapi.json \
  -o src/poi-lake-client.d.ts
```

## Support

Operational issues / key requests / bugs: file at
`https://github.com/oohx-matrix/oohx-poi/issues` (private repo —
request access from ops).
