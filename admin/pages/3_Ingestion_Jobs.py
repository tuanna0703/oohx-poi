"""Ingestion Jobs — filter recent jobs, trigger a new one."""

from __future__ import annotations

import json

import streamlit as st

from admin.lib.api import post_json
from admin.lib.queries import per_source_stats, recent_jobs

st.set_page_config(page_title="Ingestion Jobs — POI Lake", layout="wide")
st.title("Ingestion Jobs")

# ---- Trigger a new job ----------------------------------------------------

with st.expander("Trigger a new job", expanded=False):
    sources_df = per_source_stats()
    enabled = sources_df[sources_df["enabled"]]
    if enabled.empty:
        st.warning("No source is enabled. Toggle one on the Sources page first.")
    else:
        with st.form("trigger_job"):
            cols = st.columns([2, 1, 1, 1, 2])
            source_code = cols[0].selectbox(
                "Source", options=enabled["code"].tolist()
            )
            lat = cols[1].number_input("lat", value=21.0285, format="%.6f")
            lng = cols[2].number_input("lng", value=105.8542, format="%.6f")
            radius = cols[3].number_input(
                "radius_m", min_value=100, max_value=20000, value=500, step=100
            )
            category = cols[4].text_input(
                "category (optional)", value="cafe",
                help="adapter-specific label (e.g. 'cafe' for OSM, 'circle k' for gosom)",
            )
            submitted = st.form_submit_button("Submit", type="primary")
            if submitted:
                params: dict = {"lat": lat, "lng": lng, "radius_m": int(radius)}
                if category.strip():
                    params["category"] = category.strip()
                try:
                    resp = post_json(
                        "/api/v1/admin/ingestion-jobs",
                        body={
                            "source_code": source_code,
                            "job_type": "area_sweep",
                            "params": params,
                        },
                    )
                    st.success(f"Job {resp['id']} enqueued")
                    st.cache_data.clear()
                except Exception as exc:  # noqa: BLE001
                    st.error(f"Submit failed: {exc}")

# ---- Recent jobs filter ---------------------------------------------------

st.divider()
filt_cols = st.columns([1, 2, 2, 1])
status = filt_cols[0].selectbox(
    "status", ["", "pending", "running", "completed", "failed", "cancelled"]
)
source_code = filt_cols[1].text_input("source_code", value="")
limit = filt_cols[2].slider("limit", 10, 500, 100, step=10)
if filt_cols[3].button("Refresh"):
    st.cache_data.clear()

df = recent_jobs(
    limit=limit,
    status=(status or None),
    source_code=(source_code.strip() or None),
)

if df.empty:
    st.info("No jobs match the current filter.")
else:
    df["params"] = df["params"].apply(lambda v: json.dumps(v, ensure_ascii=False))
    df["stats"] = df["stats"].apply(lambda v: json.dumps(v, ensure_ascii=False))
    st.dataframe(
        df,
        use_container_width=True,
        column_order=[
            "id", "source", "job_type", "status",
            "stats", "started_at", "completed_at", "created_at", "error_message", "params",
        ],
        hide_index=True,
    )
