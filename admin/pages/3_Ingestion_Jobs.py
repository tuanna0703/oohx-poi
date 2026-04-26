"""Ingestion Jobs — single-cell or tiled sweep, with admin-unit picking
and multi-category support.

Three input modes:

  * **Single cell** — one job at a chosen lat/lng + free-text or OpenOOH
    category.
  * **Tiled · bbox** — chops a manual bbox into cells, one job per cell ×
    category.
  * **Tiled · admin unit** — pick a province (and optionally a district);
    bbox comes from ``admin_units`` and the cell grid is computed.

Categories are a multiselect over the seeded OpenOOH taxonomy. Picking
multiple categories runs each one as a separate set of jobs (cells ×
categories), bounded by ``max_jobs``.
"""

from __future__ import annotations

import json

import pandas as pd
import streamlit as st
from sqlalchemy import text

from admin.lib.api import post_json
from admin.lib.db import session as db_session
from admin.lib.queries import (
    all_brands_for_picker,
    per_source_stats,
    recent_jobs,
)
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
        "SELECT code, name, level FROM openooh_categories ORDER BY level, code"
    )
    with db_session() as s:
        return pd.read_sql(sql, s.connection())


@st.cache_data(ttl=300)
def _provinces() -> pd.DataFrame:
    sql = text(
        """
        SELECT code, name, lng_min, lat_min, lng_max, lat_max
        FROM admin_units WHERE level = 1 ORDER BY name
        """
    )
    with db_session() as s:
        return pd.read_sql(sql, s.connection())


@st.cache_data(ttl=300)
def _districts(province_code: str) -> pd.DataFrame:
    sql = text(
        """
        SELECT code, name, lng_min, lat_min, lng_max, lat_max
        FROM admin_units WHERE level = 2 AND parent_code = :p ORDER BY name
        """
    )
    with db_session() as s:
        return pd.read_sql(sql, s.connection(), params={"p": province_code})


def _bbox_for_admin(admin_code: str) -> list[float] | None:
    """Look up bbox for a province or district code."""
    sql = text(
        "SELECT lng_min, lat_min, lng_max, lat_max FROM admin_units WHERE code = :c"
    )
    with db_session() as s:
        row = s.execute(sql, {"c": admin_code}).first()
    return [float(row[0]), float(row[1]), float(row[2]), float(row[3])] if row else None


def _format_keyword_preview(source_code: str, cat: str | None) -> str:
    if source_code != "gosom_scraper" or not cat:
        return ""
    if is_openooh_code(cat):
        kws = keywords_for_openooh(cat)
        return (
            f"`{cat}` → " + ", ".join(f"`{k}`" for k in kws)
            + f"  ({len(kws)} parallel queries / cell)"
        )
    return f"verbatim keyword: `{cat}` (1 query / cell)"


# ---- Trigger a new job ----------------------------------------------------

with st.expander("Trigger a new job", expanded=True):
    sources_df = per_source_stats()
    enabled = sources_df[sources_df["enabled"]]
    if enabled.empty:
        st.warning("No source is enabled. Toggle one on the Sources page first.")
        st.stop()

    c1, c2 = st.columns([2, 3])
    source_code = c1.selectbox("Source", options=enabled["code"].tolist())
    mode = c2.radio(
        "Area",
        ["Single cell", "Tiled · admin unit", "Tiled · bbox"],
        horizontal=True,
    )

    bbox: list[float] | None = None
    admin_code: str | None = None
    lat = lng = radius = None
    cell_size_m = 4000

    if mode == "Single cell":
        c1, c2, c3 = st.columns(3)
        lat = c1.number_input("lat", value=21.0285, format="%.6f")
        lng = c2.number_input("lng", value=105.8542, format="%.6f")
        radius = c3.number_input(
            "radius_m", min_value=100, max_value=20000, value=500, step=100
        )

    elif mode == "Tiled · bbox":
        c1, c2, c3, c4, c5 = st.columns(5)
        lng_min = c1.number_input("lng_min", value=105.74, format="%.4f")
        lat_min = c2.number_input("lat_min", value=20.95, format="%.4f")
        lng_max = c3.number_input("lng_max", value=105.94, format="%.4f")
        lat_max = c4.number_input("lat_max", value=21.10, format="%.4f")
        cell_size_m = c5.number_input("cell_size_m", 500, 20000, 4000, step=500)
        bbox = [lng_min, lat_min, lng_max, lat_max]

    else:  # Tiled · admin unit
        provs = _provinces()
        c1, c2, c3 = st.columns([2, 2, 1])
        prov_label = c1.selectbox(
            "Province",
            options=[f"{r.code} — {r['name']}" for _, r in provs.iterrows()],
        )
        prov_code = prov_label.split(" — ", 1)[0]
        dists = _districts(prov_code)
        dist_options = ["(whole province)"] + [
            f"{r.code} — {r['name']}" for _, r in dists.iterrows()
        ]
        dist_label = c2.selectbox("District", options=dist_options)
        cell_size_m = c3.number_input("cell_size_m", 500, 20000, 4000, step=500)
        admin_code = (
            prov_code if dist_label == "(whole province)"
            else dist_label.split(" — ", 1)[0]
        )

    # ---- Category / brand multiselect -----------------------------------
    st.markdown("---")
    cat_mode = st.radio(
        "What to search for",
        [
            "OpenOOH codes (multiple)",
            "Brands (multiple)",
            "Free text (single)",
            "(none)",
        ],
        horizontal=True,
        help="Brands run a gosom keyword for each picked brand — useful "
             "for chain coverage like 'every Starbucks in Hà Nội'.",
    )
    chosen_categories: list[str] = []
    if cat_mode == "OpenOOH codes (multiple)":
        taxonomy = _openooh_options()
        opts = [
            f"{row.code} — {row['name']} (L{row.level})"
            for _, row in taxonomy.iterrows()
        ]
        picked = st.multiselect(
            "OpenOOH categories",
            options=opts,
            help="Each picked category becomes its own set of jobs.",
        )
        chosen_categories = [p.split(" — ", 1)[0] for p in picked]

    elif cat_mode == "Brands (multiple)":
        brands_df = all_brands_for_picker()
        # Label format: "Starbucks (hospitality)" so the user sees the
        # rough category alongside the brand name.
        labels = [
            f"{r['name']}  ·  {r.category or '-'}"
            for _, r in brands_df.iterrows()
        ]
        picked = st.multiselect(
            "Brands",
            options=labels,
            help="Each brand becomes a gosom keyword. Add the brand's "
                 "first alias too for wider coverage (toggle below).",
        )
        chosen_brands = [p.split("  ·  ", 1)[0] for p in picked]
        with_aliases = st.checkbox(
            "Also search alias names (richer coverage, more queries)",
            value=False,
        )
        for brand_name in chosen_brands:
            chosen_categories.append(brand_name)
            if with_aliases:
                row = brands_df[brands_df["name"] == brand_name].iloc[0]
                aliases = list(row.aliases) if hasattr(row.aliases, "__iter__") else []
                # Take the first non-trivial alias to avoid doubling cost.
                for alias in aliases[:1]:
                    if alias and alias != brand_name:
                        chosen_categories.append(alias)
        # Manual extra terms for brands not in the seeded table.
        extra = st.text_input(
            "Extra brand keywords (comma-separated)",
            value="",
            help="Add brands missing from the seeded list, e.g. 'Medlatec, Pacific Cross'.",
        )
        if extra.strip():
            chosen_categories.extend(
                [t.strip() for t in extra.split(",") if t.strip()]
            )

    elif cat_mode == "Free text (single)":
        txt = st.text_input("category", value="cafe").strip()
        if txt:
            chosen_categories = [txt]

    # ---- Live preview ----------------------------------------------------
    if chosen_categories and source_code == "gosom_scraper":
        for c in chosen_categories[:8]:
            st.info(_format_keyword_preview(source_code, c))

    if mode != "Single cell" and chosen_categories:
        try:
            from poi_lake.api.v1.admin import _grid_centers
            preview_bbox = bbox
            if admin_code and not preview_bbox:
                preview_bbox = _bbox_for_admin(admin_code)
            if preview_bbox:
                cells = _grid_centers(preview_bbox, int(cell_size_m))
                total = len(cells) * len(chosen_categories)
                st.caption(
                    f"≈ **{len(cells)} cells × {len(chosen_categories)} categories = "
                    f"{total} jobs** (cell radius ~{int(cell_size_m)//2}m)"
                )
        except Exception:  # noqa: BLE001
            pass

    # ---- Submit ----------------------------------------------------------
    if st.button("Submit", type="primary"):
        try:
            if mode == "Single cell":
                params: dict = {"lat": lat, "lng": lng, "radius_m": int(radius)}
                if chosen_categories:
                    params["category"] = chosen_categories[0]
                resp = post_json(
                    "/api/v1/admin/ingestion-jobs",
                    body={
                        "source_code": source_code,
                        "job_type": "area_sweep",
                        "params": params,
                    },
                )
                st.success(f"Job {resp['id']} enqueued")
            else:
                body: dict = {
                    "source_code": source_code,
                    "cell_size_m": int(cell_size_m),
                    "categories": chosen_categories,
                    "max_jobs": 300,
                }
                if mode == "Tiled · bbox":
                    body["bbox"] = bbox
                else:
                    body["admin_code"] = admin_code
                resp = post_json("/api/v1/admin/ingestion-jobs/tiled", body=body)
                st.success(
                    f"{resp['count']} jobs enqueued · "
                    f"{resp['cells']} cells × {resp['categories']} categories · "
                    f"radius {resp['cell_radius_m']}m"
                )
            st.cache_data.clear()
        except Exception as exc:  # noqa: BLE001
            st.error(f"Submit failed: {exc}")

# ---- Recent jobs filter ---------------------------------------------------

st.divider()
filt_cols = st.columns([1, 2, 2, 1])
status = filt_cols[0].selectbox(
    "status", ["", "pending", "running", "completed", "failed", "cancelled"]
)
source_filter = filt_cols[1].text_input("source filter", value="")
limit = filt_cols[2].slider("limit", 10, 500, 100, step=10)
if filt_cols[3].button("Refresh"):
    st.cache_data.clear()

df = recent_jobs(
    limit=limit,
    status=(status or None),
    source_code=(source_filter.strip() or None),
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
            "id", "source", "job_type", "status", "stats",
            "started_at", "completed_at", "created_at", "error_message", "params",
        ],
        hide_index=True,
    )
