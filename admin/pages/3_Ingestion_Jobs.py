"""Ingestion Jobs — filter recent jobs, trigger a new one.

For gosom (and any other free-text source) you can pick an OpenOOH
category code; the adapter expands it into a list of search keywords.
For OSM Overpass, type the OSM tag value (e.g. ``cafe``) directly.
"""

from __future__ import annotations

import json

import pandas as pd
import streamlit as st
from sqlalchemy import text

from admin.lib.api import post_json
from admin.lib.db import session as db_session
from admin.lib.queries import per_source_stats, recent_jobs
from poi_lake.pipeline.normalize.openooh_keywords import (
    is_openooh_code,
    keywords_for_openooh,
)

st.set_page_config(page_title="Ingestion Jobs — POI Lake", layout="wide")
st.title("Ingestion Jobs")


# ---- helpers --------------------------------------------------------------


@st.cache_data(ttl=60)
def _openooh_options() -> pd.DataFrame:
    sql = text(
        """
        SELECT code, name, level FROM openooh_categories
        ORDER BY level, code
        """
    )
    with db_session() as s:
        return pd.read_sql(sql, s.connection())


# ---- Trigger a new job ----------------------------------------------------

with st.expander("Trigger a new job", expanded=True):
    sources_df = per_source_stats()
    enabled = sources_df[sources_df["enabled"]]
    if enabled.empty:
        st.warning("No source is enabled. Toggle one on the Sources page first.")
    else:
        c1, c2, c3, c4 = st.columns([2, 1, 1, 1])
        source_code = c1.selectbox(
            "Source", options=enabled["code"].tolist(),
            help="osm_overpass uses OSM tag values; gosom_scraper translates "
                 "OpenOOH codes into Google Maps keywords.",
        )
        lat = c2.number_input("lat", value=21.0285, format="%.6f")
        lng = c3.number_input("lng", value=105.8542, format="%.6f")
        radius = c4.number_input(
            "radius_m", min_value=100, max_value=20000, value=500, step=100
        )

        # Category picker — two modes:
        #   1. OpenOOH code from the seeded taxonomy (recommended, esp. gosom)
        #   2. Free text (raw adapter category)
        mode = st.radio(
            "Category mode",
            ["OpenOOH code", "Free text"],
            horizontal=True,
            help="OpenOOH codes are translated to source-specific keywords. "
                 "Free text is forwarded as-is.",
        )

        category_value: str | None = None
        if mode == "OpenOOH code":
            taxonomy = _openooh_options()
            # Format: "retail.convenience_stores — Convenience Stores (level 2)"
            options = ["(none)"] + [
                f"{row.code} — {row['name']} (level {row.level})"
                for _, row in taxonomy.iterrows()
            ]
            chosen = st.selectbox("OpenOOH category", options=options, index=0)
            if chosen != "(none)":
                category_value = chosen.split(" — ", 1)[0]
        else:
            text_in = st.text_input(
                "category (free text)",
                value="cafe",
                help="OSM: 'cafe' / 'amenity=cafe'.  gosom: 'circle k', 'pho 24'.",
            )
            category_value = text_in.strip() or None

        # ---- Live preview of effective keywords for gosom ---------------
        if source_code == "gosom_scraper":
            if category_value and is_openooh_code(category_value):
                kws = keywords_for_openooh(category_value)
                st.info(
                    f"**gosom keywords for `{category_value}`:**  "
                    + ", ".join(f"`{k}`" for k in kws)
                    + f"  _(gosom will run **{len(kws)}** parallel queries — "
                    f"expect {len(kws)*30}-{len(kws)*60}s)_"
                )
            elif category_value:
                st.info(f"**gosom keyword:**  `{category_value}` (single query)")
            else:
                st.info(
                    "**gosom default keywords:**  `restaurant`, `cafe`, "
                    "`convenience store`, `shop`"
                )
        elif source_code == "osm_overpass" and category_value and is_openooh_code(category_value):
            st.warning(
                "OSM doesn't understand OpenOOH codes directly — pass an OSM "
                "tag value like `cafe` or `amenity=cafe` instead."
            )

        if st.button("Submit", type="primary"):
            params: dict = {"lat": lat, "lng": lng, "radius_m": int(radius)}
            if category_value:
                params["category"] = category_value
            try:
                resp = post_json(
                    "/api/v1/admin/ingestion-jobs",
                    body={
                        "source_code": source_code,
                        "job_type": "area_sweep",
                        "params": params,
                    },
                )
                st.success(f"Job {resp['id']} enqueued — watch the table below for progress.")
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
