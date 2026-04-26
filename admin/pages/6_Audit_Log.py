"""Audit Log — search master_poi_history."""

from __future__ import annotations

import json

import streamlit as st

from admin.lib.queries import audit_log

st.set_page_config(page_title="Audit Log — POI Lake", layout="wide")
st.title("Audit Log")
st.caption(
    "Every master_poi mutation is recorded in master_poi_history. "
    "Filter by master_poi UUID or by change reason."
)

cols = st.columns([3, 2, 1, 1])
master_id = cols[0].text_input("master_poi_id (UUID)", value="").strip()
change_reason = cols[1].selectbox(
    "change_reason",
    options=["", "initial_merge", "singleton_master", "new_source_added",
             "llm_resolved", "manual_edit"],
)
days = cols[2].slider("days", 1, 90, 7)
if cols[3].button("Refresh"):
    st.cache_data.clear()

df = audit_log(
    master_poi_id=(master_id or None),
    change_reason=(change_reason or None),
    days=days,
    limit=200,
)

if df.empty:
    st.info("No audit entries match the current filter.")
    st.stop()

st.dataframe(
    df.assign(
        changed_fields=df["changed_fields"].apply(lambda v: ", ".join(v) if isinstance(v, list) else str(v)),
        new_values=df["new_values"].apply(lambda v: json.dumps(v, ensure_ascii=False)),
        previous_values=df["previous_values"].apply(lambda v: json.dumps(v, ensure_ascii=False)),
    ),
    use_container_width=True,
    column_order=[
        "id", "master_poi_id", "canonical_name", "version",
        "change_reason", "changed_fields", "changed_at",
        "new_values", "previous_values",
    ],
    hide_index=True,
)

# Drill-down: pick a row → show diff
st.divider()
st.subheader("Inspect a single entry")
ids = df["id"].tolist()
chosen = st.selectbox("entry id", options=ids, format_func=lambda i: f"#{i}")
row = df[df["id"] == chosen].iloc[0]
left, right = st.columns(2)
with left:
    st.write("**Previous**")
    st.json(row["previous_values"])
with right:
    st.write("**New**")
    st.json(row["new_values"])
