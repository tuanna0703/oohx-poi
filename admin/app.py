"""Landing page — quick stack health + links to the multi-page sub-pages.

Streamlit auto-discovers pages from ``admin/pages/`` and exposes them in
the left sidebar in numeric order.
"""

from __future__ import annotations

import os

import streamlit as st
from sqlalchemy import text

from admin.lib.db import session as db_session
from admin.lib.queries import kpis

st.set_page_config(page_title="POI Lake Admin", layout="wide", page_icon=":bar_chart:")

st.title("POI Lake — Admin")
st.caption(
    f"API: {os.getenv('POI_LAKE_API_URL', 'not set')}  •  "
    f"DB: {os.getenv('DATABASE_URL', 'not set').split('@')[-1]}"
)

# ---- stack health ---------------------------------------------------------

with st.spinner("Checking stack health..."):
    try:
        with db_session() as s:
            db_ok = s.execute(text("SELECT 1")).scalar() == 1
            extensions = {
                row[0]
                for row in s.execute(
                    text(
                        "SELECT extname FROM pg_extension "
                        "WHERE extname IN ('postgis','vector','pgcrypto')"
                    )
                )
            }
        col_a, col_b, col_c, col_d = st.columns(4)
        col_a.metric("DB", "OK" if db_ok else "down")
        col_b.metric("postgis", "ok" if "postgis" in extensions else "MISSING")
        col_c.metric("pgvector", "ok" if "vector" in extensions else "MISSING")
        col_d.metric("pgcrypto", "ok" if "pgcrypto" in extensions else "MISSING")
    except Exception as exc:  # noqa: BLE001
        st.error(f"DB unreachable: {exc}")

# ---- top-level KPIs -------------------------------------------------------

st.subheader("Pipeline KPIs")
try:
    k = kpis()
    cols = st.columns(4)
    cols[0].metric("Raw POIs", f"{k['raw_total']:,}")
    cols[1].metric("Processed", f"{k['processed_total']:,}",
                   delta=f"{k['pending']:,} pending" if k["pending"] else "0 pending")
    cols[2].metric("Master records", f"{k['masters_active']:,}",
                   delta=f"{k['masters_multi_source']:,} multi-source")
    cols[3].metric("Jobs in flight", f"{k['jobs_inflight']:,}",
                   delta=f"{k['jobs_failed']:,} failed",
                   delta_color="inverse")
except Exception as exc:  # noqa: BLE001
    st.error(f"KPI query failed: {exc}")

st.divider()

# ---- navigation hint ------------------------------------------------------

st.markdown(
    """
**Pages** (use the sidebar):

| Page | What it's for |
|---|---|
| Dashboard | Per-source breakdown, ingestion rate, job status |
| Sources | Toggle adapters on/off, view config |
| Ingestion Jobs | Filter recent jobs, trigger new sweeps |
| POI Explorer | Map view of master records with filters |
| Dedupe Queue | Review ambiguous clusters, force-merge or reject |
| Audit Log | Search master_poi_history for changes |

Reads come straight from PostgreSQL (cached for ~10s); writes go through
the admin API at `POI_LAKE_API_URL`.
"""
)
