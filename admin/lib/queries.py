"""Cached SQL queries used by Streamlit pages.

All read-only. Cache TTLs are short (5-30s) so a refresh shows fresh data
within a few seconds without overloading the DB on every interaction.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import pandas as pd
import streamlit as st
from sqlalchemy import text

from admin.lib.db import session as _session


def _df(sql: str, **params: Any) -> pd.DataFrame:
    with _session() as s:
        return pd.read_sql(text(sql), s.connection(), params=params)


# --------------------------------------------------------------- dashboard


@st.cache_data(ttl=10)
def kpis() -> dict[str, int]:
    sql = """
    SELECT
      (SELECT COUNT(*) FROM raw_pois)                                            AS raw_total,
      (SELECT COUNT(*) FROM processed_pois)                                      AS processed_total,
      (SELECT COUNT(*) FROM processed_pois WHERE merge_status = 'pending')       AS pending,
      (SELECT COUNT(*) FROM processed_pois WHERE merge_status = 'rejected')      AS rejected,
      (SELECT COUNT(*) FROM master_pois WHERE status = 'active')                 AS masters_active,
      (SELECT COUNT(*) FROM master_pois WHERE sources_count > 1)                 AS masters_multi_source,
      (SELECT COUNT(*) FROM ingestion_jobs WHERE status IN ('pending','running')) AS jobs_inflight,
      (SELECT COUNT(*) FROM ingestion_jobs WHERE status = 'failed')              AS jobs_failed
    """
    return _df(sql).iloc[0].to_dict()


@st.cache_data(ttl=10)
def per_source_stats() -> pd.DataFrame:
    sql = """
    SELECT
      s.code, s.name, s.enabled, s.priority,
      COUNT(r.id) AS raw_count,
      MAX(r.fetched_at) AS last_fetched
    FROM sources s
    LEFT JOIN raw_pois r ON r.source_id = s.id
    GROUP BY s.id, s.code, s.name, s.enabled, s.priority
    ORDER BY s.priority
    """
    return _df(sql)


@st.cache_data(ttl=15)
def hourly_ingestion_24h() -> pd.DataFrame:
    sql = """
    SELECT date_trunc('hour', fetched_at) AS hour, COUNT(*) AS rows_in
    FROM raw_pois
    WHERE fetched_at >= NOW() - INTERVAL '24 hours'
    GROUP BY 1 ORDER BY 1
    """
    return _df(sql)


@st.cache_data(ttl=15)
def jobs_status_breakdown() -> pd.DataFrame:
    sql = """
    SELECT status, COUNT(*) AS n FROM ingestion_jobs GROUP BY status ORDER BY n DESC
    """
    return _df(sql)


# --------------------------------------------------------------- ingestion-jobs


@st.cache_data(ttl=5)
def recent_jobs(limit: int = 100, status: str | None = None,
                source_code: str | None = None) -> pd.DataFrame:
    where = []
    params: dict[str, Any] = {"lim": limit}
    if status:
        where.append("j.status = :st")
        params["st"] = status
    if source_code:
        where.append("s.code = :sc")
        params["sc"] = source_code
    where_sql = " WHERE " + " AND ".join(where) if where else ""
    sql = f"""
    SELECT j.id, s.code AS source, j.job_type, j.status,
           j.params, j.stats,
           j.started_at, j.completed_at, j.error_message,
           j.created_at
    FROM ingestion_jobs j JOIN sources s ON s.id = j.source_id
    {where_sql}
    ORDER BY j.created_at DESC
    LIMIT :lim
    """
    return _df(sql, **params)


# --------------------------------------------------------------- master_pois


@st.cache_data(ttl=10)
def master_pois_for_map(
    *,
    lat: float | None = None,
    lng: float | None = None,
    radius_m: int | None = None,
    category: str | None = None,
    brand: str | None = None,
    province_code: str | None = None,
    district_code: str | None = None,
    min_confidence: float = 0.0,
    limit: int = 500,
) -> pd.DataFrame:
    clauses = ["status = 'active'", "confidence >= :mc"]
    params: dict[str, Any] = {"mc": min_confidence, "lim": limit}
    if lat is not None and lng is not None and radius_m is not None:
        clauses.append("ST_DWithin(location, ST_GeogFromText(:p), :r)")
        params["p"] = f"SRID=4326;POINT({lng} {lat})"
        params["r"] = radius_m
    if category:
        clauses.append("(openooh_category = :cat OR openooh_subcategory = :cat)")
        params["cat"] = category
    if brand:
        clauses.append("brand = :brand")
        params["brand"] = brand
    if province_code:
        clauses.append("province_code = :prov")
        params["prov"] = province_code
    if district_code:
        clauses.append("district_code = :dist")
        params["dist"] = district_code
    where = " AND ".join(clauses)
    sql = f"""
    SELECT id, canonical_name, brand,
           openooh_category, openooh_subcategory,
           ST_Y(location::geometry) AS lat,
           ST_X(location::geometry) AS lng,
           sources_count, confidence,
           canonical_address, canonical_phone, canonical_website,
           province_code, district_code
    FROM master_pois
    WHERE {where}
    ORDER BY confidence DESC, updated_at DESC
    LIMIT :lim
    """
    return _df(sql, **params)


@st.cache_data(ttl=30)
def admin_provinces() -> pd.DataFrame:
    return _df(
        "SELECT code, name FROM admin_units WHERE level = 1 ORDER BY name"
    )


@st.cache_data(ttl=30)
def admin_districts(province_code: str | None) -> pd.DataFrame:
    if not province_code:
        return pd.DataFrame(columns=["code", "name"])
    return _df(
        "SELECT code, name FROM admin_units WHERE level=2 AND parent_code=:p ORDER BY name",
        p=province_code,
    )


@st.cache_data(ttl=15)
def brand_summary(
    *,
    province_code: str | None = None,
    district_code: str | None = None,
    category: str | None = None,
    min_confidence: float = 0.0,
    limit: int = 200,
) -> pd.DataFrame:
    clauses = ["status='active'", "brand IS NOT NULL", "confidence >= :mc"]
    params: dict[str, Any] = {"mc": min_confidence, "lim": limit}
    if province_code:
        clauses.append("province_code = :prov")
        params["prov"] = province_code
    if district_code:
        clauses.append("district_code = :dist")
        params["dist"] = district_code
    if category:
        clauses.append("(openooh_category = :cat OR openooh_subcategory = :cat)")
        params["cat"] = category
    where = " AND ".join(clauses)
    sql = f"""
    SELECT
        brand,
        COUNT(*) AS n,
        (ARRAY_AGG(openooh_subcategory ORDER BY openooh_subcategory)
            FILTER (WHERE openooh_subcategory IS NOT NULL))[1] AS category,
        AVG(confidence) AS avg_confidence
    FROM master_pois
    WHERE {where}
    GROUP BY brand
    ORDER BY n DESC, brand ASC
    LIMIT :lim
    """
    return _df(sql, **params)


@st.cache_data(ttl=30)
def categories_with_counts() -> pd.DataFrame:
    return _df(
        """
        SELECT COALESCE(openooh_subcategory, openooh_category, '(none)') AS category,
               COUNT(*) AS n
        FROM master_pois WHERE status = 'active'
        GROUP BY 1 ORDER BY n DESC
        """
    )


@st.cache_data(ttl=30)
def brands_with_counts() -> pd.DataFrame:
    return _df(
        """
        SELECT brand, COUNT(*) AS n FROM master_pois
        WHERE brand IS NOT NULL AND status = 'active'
        GROUP BY brand ORDER BY n DESC
        """
    )


# --------------------------------------------------------------- dedupe queue


@st.cache_data(ttl=5)
def pending_clusters(eps_meters: int = 55) -> pd.DataFrame:
    """Cluster currently-pending processed_pois and return one row per
    cluster that has 2+ members — the candidates for review."""
    sql = """
    WITH clustered AS (
      SELECT id, raw_poi_id, name_original, address_normalized, brand,
             phone_e164, website_domain, openooh_subcategory,
             ST_Y(location::geometry) AS lat,
             ST_X(location::geometry) AS lng,
             quality_score,
             ST_ClusterDBSCAN(
               ST_Transform(location::geometry, 3857),
               eps := :eps, minpoints := 1
             ) OVER () AS cluster_id
      FROM processed_pois WHERE merge_status = 'pending'
    )
    SELECT * FROM clustered
    WHERE cluster_id IN (
      SELECT cluster_id FROM clustered GROUP BY cluster_id HAVING COUNT(*) > 1
    )
    ORDER BY cluster_id, quality_score DESC NULLS LAST
    """
    return _df(sql, eps=eps_meters)


# --------------------------------------------------------------- audit log


@st.cache_data(ttl=5)
def audit_log(
    *,
    master_poi_id: str | None = None,
    change_reason: str | None = None,
    days: int = 7,
    limit: int = 200,
) -> pd.DataFrame:
    clauses = ["changed_at >= :since"]
    params: dict[str, Any] = {
        "since": datetime.now(timezone.utc) - timedelta(days=days),
        "lim": limit,
    }
    if master_poi_id:
        clauses.append("master_poi_id = :mid")
        params["mid"] = master_poi_id
    if change_reason:
        clauses.append("change_reason = :cr")
        params["cr"] = change_reason
    where = " AND ".join(clauses)
    sql = f"""
    SELECT h.id, h.master_poi_id, h.version, h.change_reason, h.changed_at,
           h.changed_fields, h.previous_values, h.new_values,
           m.canonical_name
    FROM master_poi_history h
    LEFT JOIN master_pois m ON m.id = h.master_poi_id
    WHERE {where}
    ORDER BY h.changed_at DESC LIMIT :lim
    """
    return _df(sql, **params)
