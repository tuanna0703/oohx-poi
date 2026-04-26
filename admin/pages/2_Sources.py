"""Sources — list adapters, toggle enabled, view config."""

from __future__ import annotations

import json

import streamlit as st
from sqlalchemy import select, text, update

from admin.lib.db import session as db_session
from poi_lake.db.models import Source

st.set_page_config(page_title="Sources — POI Lake", layout="wide")
st.title("Sources")
st.caption(
    "Adapters that supply raw POIs. Toggle ``enabled`` here; new ingestion "
    "jobs only run when the source is enabled."
)

with db_session() as s:
    sources = s.execute(select(Source).order_by(Source.priority)).scalars().all()
    rows = [
        {
            "id": src.id,
            "code": src.code,
            "name": src.name,
            "adapter_class": src.adapter_class,
            "enabled": src.enabled,
            "priority": src.priority,
            "config": src.config or {},
            "updated_at": src.updated_at,
        }
        for src in sources
    ]

if not rows:
    st.info("No sources seeded. Run ``scripts/seed_sources.py``.")
    st.stop()

# Two-column layout: left = list, right = detail of selected.
left, right = st.columns([3, 4])

with left:
    selected_idx = st.radio(
        "Source",
        options=range(len(rows)),
        format_func=lambda i: (
            f"{'✓' if rows[i]['enabled'] else '○'}  {rows[i]['code']}  "
            f"(p={rows[i]['priority']})"
        ),
        label_visibility="collapsed",
    )

selected = rows[selected_idx]

with right:
    st.subheader(f"{selected['code']} — {selected['name']}")
    st.caption(f"adapter: `{selected['adapter_class']}`")

    new_enabled = st.toggle("Enabled", value=selected["enabled"])
    if new_enabled != selected["enabled"]:
        if st.button("Save", type="primary"):
            with db_session() as s:
                s.execute(
                    update(Source)
                    .where(Source.id == selected["id"])
                    .values(enabled=new_enabled)
                )
                s.commit()
            st.success(f"{selected['code']} → enabled={new_enabled}")
            st.cache_data.clear()
            st.rerun()

    st.write(f"**Priority:** {selected['priority']}  "
             f"(lower number = wins canonical-value tie-breaks)")
    st.write(f"**Last updated:** {selected['updated_at']}")
    st.subheader("Config")
    st.code(json.dumps(selected["config"], indent=2, ensure_ascii=False), language="json")
