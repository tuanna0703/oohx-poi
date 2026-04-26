"""Dashboard — KPIs, per-source breakdown, ingestion rate, job status."""

from __future__ import annotations

import streamlit as st

from admin.lib.queries import (
    hourly_ingestion_24h,
    jobs_status_breakdown,
    kpis,
    per_source_stats,
)

st.set_page_config(page_title="Dashboard — POI Lake", layout="wide")
st.title("Dashboard")

if st.button("Refresh"):
    st.cache_data.clear()

# ---- KPI strip ------------------------------------------------------------

k = kpis()
c = st.columns(8)
c[0].metric("Raw POIs", f"{k['raw_total']:,}")
c[1].metric("Processed", f"{k['processed_total']:,}")
c[2].metric("Pending", f"{k['pending']:,}")
c[3].metric("Rejected", f"{k['rejected']:,}")
c[4].metric("Masters (active)", f"{k['masters_active']:,}")
c[5].metric("Multi-source", f"{k['masters_multi_source']:,}")
c[6].metric("Jobs in flight", f"{k['jobs_inflight']:,}")
c[7].metric("Jobs failed", f"{k['jobs_failed']:,}")

st.divider()

# ---- per-source breakdown -------------------------------------------------

left, right = st.columns([3, 2])

with left:
    st.subheader("Per-source raw_pois")
    df = per_source_stats()
    st.dataframe(
        df,
        use_container_width=True,
        column_config={
            "code": "Source",
            "name": "Name",
            "enabled": st.column_config.CheckboxColumn("Enabled"),
            "priority": st.column_config.NumberColumn("Priority"),
            "raw_count": st.column_config.NumberColumn("Raw rows", format="%d"),
            "last_fetched": st.column_config.DatetimeColumn("Last fetch"),
        },
        hide_index=True,
    )

with right:
    st.subheader("Job status")
    js = jobs_status_breakdown()
    if js.empty:
        st.info("No ingestion jobs yet.")
    else:
        st.bar_chart(js.set_index("status")["n"])

st.divider()

# ---- 24h ingestion rate ---------------------------------------------------

st.subheader("Last 24h ingestion (raw_pois per hour)")
hourly = hourly_ingestion_24h()
if hourly.empty:
    st.info("No raw_pois in the last 24 hours.")
else:
    st.bar_chart(hourly.set_index("hour")["rows_in"])
