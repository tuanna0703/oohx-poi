"""Raw POIs — bronze layer browser for adapter debugging.

What an adapter actually pulled in, before normalize/dedupe touched it.
Filter by source, ingestion job, processed/unprocessed, bbox, or recency.
Click a row to see the full ``raw_payload`` JSON — useful when a
normalize rule misfires and you need to confirm what the upstream
returned.
"""

from __future__ import annotations

import json

import pandas as pd
import streamlit as st

from admin.lib.api import APIError, get_json
from admin.lib.queries import per_source_stats

st.set_page_config(page_title="Raw POIs — POI Lake", layout="wide")
st.title("Raw POIs")
st.caption(
    "Bronze layer — raw payloads from each source, append-only. Use "
    "this to verify what an adapter ingested. For curated, deduplicated "
    "data hit *POI Explorer* instead."
)


# ---- filters --------------------------------------------------------------

with st.sidebar:
    st.header("Filters")

    sources_df = per_source_stats()
    source_options = ["(any)"] + sources_df["code"].tolist()
    source_choice = st.selectbox("Source", options=source_options)
    source_code = None if source_choice == "(any)" else source_choice

    job_id_str = st.text_input(
        "Ingestion job ID",
        value="",
        help="Drill into one job's results. Find IDs on the *Ingestion Jobs* page.",
    )
    ingestion_job_id: int | None = None
    if job_id_str.strip():
        try:
            ingestion_job_id = int(job_id_str.strip())
        except ValueError:
            st.error("ingestion job ID must be an integer")

    processed_choice = st.radio(
        "Processed?",
        options=["(any)", "yes", "no (pending)"],
        index=0,
        horizontal=False,
    )
    processed_param: bool | None = (
        True if processed_choice == "yes"
        else False if processed_choice.startswith("no")
        else None
    )

    has_loc_choice = st.radio(
        "Has location?",
        options=["(any)", "yes", "no (missing coords)"],
        index=0,
        horizontal=False,
    )
    has_location_param: bool | None = (
        True if has_loc_choice == "yes"
        else False if has_loc_choice.startswith("no")
        else None
    )

    use_bbox = st.checkbox("Filter by bounding box", value=False)
    bbox_param: str | None = None
    if use_bbox:
        c1, c2 = st.columns(2)
        lng_min = c1.number_input("lng_min", value=105.74, format="%.4f")
        lat_min = c2.number_input("lat_min", value=20.95, format="%.4f")
        lng_max = c1.number_input("lng_max", value=105.94, format="%.4f")
        lat_max = c2.number_input("lat_max", value=21.10, format="%.4f")
        bbox_param = f"{lng_min},{lat_min},{lng_max},{lat_max}"

    recency_hours = st.slider(
        "Fetched within last N hours (0 = no limit)", 0, 168, 0, step=1
    )
    fetched_since_param: str | None = None
    if recency_hours > 0:
        from datetime import datetime, timedelta, timezone
        fetched_since_param = (
            datetime.now(timezone.utc) - timedelta(hours=recency_hours)
        ).isoformat()

    per_page = st.slider("Rows per page", 10, 500, 50, step=10)
    page = st.number_input("Page", min_value=1, value=1, step=1)

    if st.button("Refresh"):
        st.rerun()


# ---- fetch ----------------------------------------------------------------

params: dict[str, object] = {"page": int(page), "per_page": int(per_page)}
if source_code:
    params["source_code"] = source_code
if ingestion_job_id is not None:
    params["ingestion_job_id"] = ingestion_job_id
if processed_param is not None:
    params["processed"] = "true" if processed_param else "false"
if has_location_param is not None:
    params["has_location"] = "true" if has_location_param else "false"
if bbox_param:
    params["bbox"] = bbox_param
if fetched_since_param:
    params["fetched_since"] = fetched_since_param

try:
    data = get_json("/api/v1/admin/raw-pois", params=params)
except APIError as exc:
    st.error(f"Fetch failed: {exc}")
    st.stop()
except Exception as exc:  # noqa: BLE001
    st.error(f"Fetch failed: {exc}")
    st.stop()

items = data.get("items", [])
total = int(data.get("total", 0))

# ---- KPIs -----------------------------------------------------------------

c1, c2, c3 = st.columns(3)
c1.metric("Total matching", f"{total:,}")
c2.metric("Showing", f"{len(items):,}")
c3.metric("Page", f"{page} of {max(1, (total + per_page - 1) // per_page)}")

if not items:
    st.info("No raw POIs match the current filters.")
    st.stop()

# ---- Table ---------------------------------------------------------------

# Flatten — show common payload fields as columns; keep the full payload
# behind a JSON viewer below.
def _shorthand(payload: dict) -> str:
    name = payload.get("name") or payload.get("title") or ""
    addr = (
        payload.get("address")
        or payload.get("formatted_address")
        or payload.get("display_name")
        or ""
    )
    return f"{name}  ·  {addr}"[:120] if (name or addr) else ""


df = pd.DataFrame([
    {
        "id": r["id"],
        "source": r.get("source_code"),
        "source_poi_id": r["source_poi_id"],
        "name/address": _shorthand(r["raw_payload"]),
        "lat": r.get("lat"),
        "lng": r.get("lng"),
        "fetched_at": r["fetched_at"],
        "processed_at": r.get("processed_at"),
        "ingestion_job_id": r.get("ingestion_job_id"),
        "content_hash": r["content_hash"][:12] + "…",
    }
    for r in items
])

st.dataframe(
    df,
    use_container_width=True,
    hide_index=True,
    column_config={
        "id": st.column_config.NumberColumn("ID", format="%d"),
        "fetched_at": st.column_config.DatetimeColumn("Fetched", format="YYYY-MM-DD HH:mm"),
        "processed_at": st.column_config.DatetimeColumn("Processed", format="YYYY-MM-DD HH:mm"),
        "ingestion_job_id": st.column_config.NumberColumn("Job", format="%d"),
    },
)

# ---- Detail viewer -------------------------------------------------------

st.divider()
st.subheader("Inspect raw payload")

selected_id = st.selectbox(
    "Pick a raw POI ID from the page above",
    options=[r["id"] for r in items],
    format_func=lambda i: f"{i} — {next((_shorthand(r['raw_payload']) for r in items if r['id'] == i), '')}",
)
if selected_id is not None:
    detail = next((r for r in items if r["id"] == selected_id), None)
    if detail is not None:
        meta_col, json_col = st.columns([1, 2])
        with meta_col:
            st.markdown("**Metadata**")
            st.write({
                "source": detail.get("source_code"),
                "source_poi_id": detail["source_poi_id"],
                "lat,lng": (
                    f"{detail['lat']:.6f}, {detail['lng']:.6f}"
                    if detail.get("lat") is not None and detail.get("lng") is not None
                    else "(no coords)"
                ),
                "fetched_at": detail["fetched_at"],
                "processed_at": detail.get("processed_at") or "(pending)",
                "ingestion_job_id": detail.get("ingestion_job_id"),
                "content_hash": detail["content_hash"],
            })
        with json_col:
            st.markdown("**raw_payload**")
            st.json(detail["raw_payload"])

        # Quick re-export so the user can grab a single record for a bug report.
        st.download_button(
            label="Download as JSON",
            data=json.dumps(detail, ensure_ascii=False, indent=2, default=str),
            file_name=f"raw_poi_{selected_id}.json",
            mime="application/json",
        )
