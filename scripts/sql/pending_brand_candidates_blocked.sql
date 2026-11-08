-- Read-only: pending brand candidates scoring at least 70, and why each was
-- not added on its own (the same tests as brand_discovery.blocked_by).
--   psql -U poi poi_lake -f scripts/sql/pending_brand_candidates_blocked.sql
-- Thresholds come from app_settings('brand_discovery'), defaults as in
-- services/app_settings.py when a key was never saved.
WITH s AS (
    SELECT
        COALESCE((v->>'auto_add')::boolean, false) AS auto_add,
        COALESCE((v->>'add_at')::int, 80)          AS add_at,
        COALESCE((v->>'min_stores')::int, 15)      AS min_stores,
        COALESCE((v->>'min_provinces')::int, 3)    AS min_provinces
    FROM (SELECT (SELECT value FROM app_settings WHERE key = 'brand_discovery') AS v) x
)
SELECT
    c.id, c.name, c.score, c.stores, c.provinces,
    c.ai->>'is_chain'       AS ai_is_chain,
    c.ai->>'existing_brand' AS existing_brand,
    concat_ws(', ',
        CASE WHEN NOT s.auto_add THEN 'auto_add_off' END,
        CASE WHEN c.score < s.add_at THEN format('score %s<%s', c.score, s.add_at) END,
        CASE WHEN c.stores < s.min_stores THEN format('stores %s<%s', c.stores, s.min_stores) END,
        CASE WHEN c.provinces < s.min_provinces THEN format('provinces %s<%s', c.provinces, s.min_provinces) END,
        CASE WHEN c.ai IS NULL OR c.ai = 'null'::jsonb THEN 'ai_missing'
             WHEN NOT COALESCE((c.ai->>'is_chain')::boolean, false) THEN 'ai_not_chain'
             WHEN c.ai ? 'existing_brand' AND c.ai->>'existing_brand' IS NOT NULL
                  THEN 'existing_brand ' || (c.ai->>'existing_brand') END,
        CASE WHEN EXISTS (SELECT 1 FROM jsonb_array_elements(c.signals) e
                          WHERE e->>'label' = 'Tên chung chung')
             THEN 'generic' END
    ) AS blocked_by
FROM brand_candidates c CROSS JOIN s
WHERE c.status = 'pending' AND c.score >= 70
ORDER BY c.score DESC, c.stores DESC;
