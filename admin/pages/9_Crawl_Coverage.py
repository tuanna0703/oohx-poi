"""Crawl Coverage — dashboard for the OpenOOH × province gosom sweep.

Sections:
  * KPI strip — pending/in-progress/done/failed totals, POIs collected,
    estimated completion time
  * Initialize / control — populate the matrix, pause, resume, retry-failed
  * Heatmap — coverage % per (province × OpenOOH level-1 category)
  * Velocity chart — POIs/hour, jobs/hour over the last 24h
  * Failed table — drill into rows that need retry
  * Detail browser — full per-cell matrix with filters
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import pandas as pd
import streamlit as st

from admin.lib.api import APIError, get_json, post_json

st.set_page_config(page_title="Crawl Coverage — POI Lake", layout="wide")
st.title("Crawl Coverage")
st.caption(
    "Tracks the OpenOOH × province gosom sweep. Each (province × OpenOOH "
    "code) is one *plan row*; the planner worker drains them at "
    "``CRAWL_RATE_PER_HOUR``-friendly rate."
)


# ---- helpers --------------------------------------------------------------

@st.cache_data(ttl=15)
def _status() -> dict[str, Any]:
    return get_json("/api/v1/admin/crawl-plan/status")


@st.cache_data(ttl=20)
def _matrix() -> list[dict[str, Any]]:
    return get_json("/api/v1/admin/crawl-plan/matrix")


@st.cache_data(ttl=30)
def _velocity(hours: int) -> list[dict[str, Any]]:
    return get_json("/api/v1/admin/crawl-plan/velocity", params={"hours": hours})


@st.cache_data(ttl=30)
def _failed() -> list[dict[str, Any]]:
    return get_json("/api/v1/admin/crawl-plan/failed", params={"limit": 200})


def _humanize_dt(dt_str: str | None) -> str:
    if not dt_str:
        return "—"
    try:
        dt = datetime.fromisoformat(dt_str.replace("Z", "+00:00"))
    except ValueError:
        return dt_str
    delta = datetime.now(timezone.utc) - dt
    secs = int(delta.total_seconds())
    if secs < 60:
        return f"{secs}s ago"
    if secs < 3600:
        return f"{secs // 60}m ago"
    if secs < 86400:
        return f"{secs // 3600}h ago"
    return f"{secs // 86400}d ago"


# ---- KPI strip ------------------------------------------------------------

try:
    s = _status()
except APIError as exc:
    st.error(f"Status fetch failed: {exc}")
    st.stop()

c1, c2, c3, c4, c5, c6 = st.columns(6)
total = int(s.get("total", 0))
done = int(s.get("done", 0))
pending = int(s.get("pending", 0))
in_progress = int(s.get("in_progress", 0))
failed = int(s.get("failed", 0))
paused = int(s.get("paused", 0))
pct = (done / total * 100) if total else 0.0

c1.metric("Total", f"{total:,}")
c2.metric("Done", f"{done:,}", delta=f"{pct:.1f}%")
c3.metric("In progress", f"{in_progress:,}")
c4.metric("Pending", f"{pending:,}")
c5.metric("Failed", f"{failed:,}")
c6.metric("Paused", f"{paused:,}")

c1, c2, c3 = st.columns(3)
c1.metric("Raw POIs collected", f"{int(s.get('pois_raw_total', 0)):,}")
c2.metric("Master POIs (linked)", f"{int(s.get('pois_master_total', 0)):,}")
last = s.get("last_completed_at")
eta = s.get("estimated_completion_at")
c3.metric("Last completion", _humanize_dt(last))
if eta:
    st.caption(f"Estimated full coverage at this rate: **{eta[:16]}** UTC")
else:
    st.caption("Estimated completion: not enough velocity data yet.")

st.divider()

# ---- Initialize / controls ------------------------------------------------

with st.expander("Controls — initialize / pause / retry", expanded=(total == 0)):
    cc1, cc2, cc3, cc4 = st.columns(4)

    if cc1.button("Initialize matrix (seed)", help="Populate plan rows for every province × every OpenOOH code. Skips existing rows unless overwrite ticked."):
        ow = st.session_state.get("init_overwrite", False)
        try:
            resp = post_json(
                "/api/v1/admin/crawl-plan/initialize",
                body={"overwrite": ow, "cell_size_m": 5000},
            )
            st.success(
                f"inserted={resp['inserted']} skipped={resp['skipped']} "
                f"overwritten={resp['overwritten']} total={resp['total']}"
            )
            st.cache_data.clear()
        except APIError as exc:
            st.error(f"initialize failed: {exc}")

    st.session_state.setdefault("init_overwrite", False)
    cc1.checkbox(
        "Overwrite existing (DESTRUCTIVE — clears progress)",
        key="init_overwrite",
    )

    if cc2.button("⏸  Pause all pending"):
        try:
            resp = post_json("/api/v1/admin/crawl-plan/pause")
            st.success(f"paused {resp['paused_rows']} rows")
            st.cache_data.clear()
        except APIError as exc:
            st.error(f"pause failed: {exc}")

    if cc3.button("▶  Resume paused"):
        try:
            resp = post_json("/api/v1/admin/crawl-plan/resume")
            st.success(f"resumed {resp['resumed_rows']} rows")
            st.cache_data.clear()
        except APIError as exc:
            st.error(f"resume failed: {exc}")

    if cc4.button("⟳  Retry failed"):
        try:
            resp = post_json("/api/v1/admin/crawl-plan/retry-failed")
            st.success(f"retried {resp['retried_rows']} rows")
            st.cache_data.clear()
        except APIError as exc:
            st.error(f"retry failed: {exc}")

    st.markdown("---")
    cd1, cd2 = st.columns(2)
    if cd1.button("⟳ Force planner tick", help="Kick off one planner pass immediately (debug)."):
        try:
            resp = post_json("/api/v1/admin/crawl-plan/tick")
            st.success(f"planner enqueued: {resp.get('message_id')}")
        except APIError as exc:
            st.error(f"tick failed: {exc}")
    if cd2.button("Refresh now"):
        st.cache_data.clear()
        st.rerun()


st.divider()

# ---- Heatmap (province × openooh level-1) --------------------------------

st.subheader("Coverage heatmap — province × OpenOOH (level-1 grouped)")
st.caption(
    "Each cell shows % done within that (province × top-level category). "
    "Hover for raw counts. Click 'Detail browser' below for level-2 drill-down."
)

try:
    matrix_rows = _matrix()
except APIError as exc:
    st.error(f"Matrix fetch failed: {exc}")
    st.stop()

if not matrix_rows:
    st.info("No plan rows yet — click *Initialize matrix* above to seed.")
else:
    df = pd.DataFrame(matrix_rows)
    # Aggregate to level-1 OpenOOH (everything before the dot).
    df["openooh_l1"] = df["openooh_code"].str.split(".").str[0]
    agg = df.groupby(["province_name", "openooh_l1"]).agg(
        cells_done=("cells_done", "sum"),
        cells_total=("cells_total", "sum"),
        pois_raw=("pois_raw", "sum"),
        rows=("openooh_code", "count"),
        rows_done=("status", lambda s: (s == "done").sum()),
    ).reset_index()
    agg["pct"] = (agg["rows_done"] / agg["rows"] * 100).round(1)

    pivot = agg.pivot_table(
        index="province_name",
        columns="openooh_l1",
        values="pct",
        fill_value=0,
    )

    # Sort rows by overall completion descending.
    pivot["__avg"] = pivot.mean(axis=1)
    pivot = pivot.sort_values("__avg", ascending=False).drop(columns="__avg")

    def _heatmap_color(v: float) -> str:
        """Red (0%) → yellow (50%) → green (100%) without matplotlib.

        Linear interpolation through HSL hue 0° → 60° → 120°. Light
        background so dark text stays readable.
        """
        try:
            v = float(v)
        except (TypeError, ValueError):
            return ""
        v = max(0.0, min(100.0, v))
        # 0% = hue 0 (red), 100% = hue 120 (green)
        hue = int(v * 1.2)
        # Pastel: 70% lightness, 65% saturation
        return f"background-color: hsl({hue}, 65%, 70%); color: #222"

    st.dataframe(
        pivot.style.map(_heatmap_color).format("{:.0f}%"),
        use_container_width=True,
        height=600,
    )

st.divider()

# ---- Velocity chart -------------------------------------------------------

st.subheader("Velocity — last 24h")
hours = st.slider("Window (hours)", 1, 168, 24, step=1)
try:
    vel = _velocity(hours)
except APIError as exc:
    st.error(f"Velocity fetch failed: {exc}")
    vel = []

if vel:
    vdf = pd.DataFrame(vel)
    vdf["hour"] = pd.to_datetime(vdf["hour"])
    vdf = vdf.set_index("hour")
    st.line_chart(vdf[["pois_raw", "jobs_completed", "jobs_failed"]])
else:
    st.info("No completed jobs in this window.")

st.divider()

# ---- Failed rows ---------------------------------------------------------

st.subheader("Failed plan rows")
try:
    failed_rows = _failed()
except APIError as exc:
    st.error(f"Failed-rows fetch error: {exc}")
    failed_rows = []

if not failed_rows:
    st.success("No failed plan rows currently.")
else:
    fdf = pd.DataFrame(failed_rows)
    st.dataframe(
        fdf[[
            "province_name", "openooh_code", "cells_done", "cells_total",
            "cells_failed", "last_attempt_at", "error_summary",
        ]],
        use_container_width=True,
        hide_index=True,
        height=300,
    )
    st.caption(
        f"**{len(failed_rows)}** failed rows. Click ⟳ Retry failed in "
        "Controls above to reset all to pending."
    )

st.divider()

# ---- Detail browser (full table with filters) ----------------------------

st.subheader("Detail browser — every plan row")
fc1, fc2, fc3 = st.columns([2, 2, 1])
prov_filter = fc1.text_input("Filter by province name (substring)")
status_filter = fc2.selectbox(
    "Status",
    options=["(any)", "pending", "in_progress", "done", "failed", "paused"],
)
limit = fc3.slider("Show rows", 50, 2000, 200, step=50)

if matrix_rows:
    df_all = pd.DataFrame(matrix_rows)
    if prov_filter:
        df_all = df_all[df_all["province_name"].str.contains(prov_filter, case=False, na=False)]
    if status_filter != "(any)":
        df_all = df_all[df_all["status"] == status_filter]
    st.dataframe(
        df_all[[
            "province_name", "openooh_code", "status",
            "cells_done", "cells_total", "cells_failed",
            "pois_raw", "pois_master", "last_attempt_at",
        ]].head(limit),
        use_container_width=True,
        hide_index=True,
        height=400,
    )
    st.caption(f"Showing {min(len(df_all), limit)} of {len(df_all)} matching rows.")
