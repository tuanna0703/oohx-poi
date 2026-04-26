"""Dedupe Queue — review pending clusters, force-merge or reject.

The page re-clusters pending processed_pois on demand (DBSCAN with
configurable eps) and shows every cluster with 2+ members. For each
cluster the operator can:

  * Merge selected — force-merge the picked rows into one master_poi.
  * Reject selected — mark the picked rows as ``merge_status='rejected'``
    so they are excluded from future automatic dedupe passes.
"""

from __future__ import annotations

import streamlit as st

from admin.lib.api import post_json
from admin.lib.queries import pending_clusters

st.set_page_config(page_title="Dedupe Queue — POI Lake", layout="wide")
st.title("Dedupe Queue")
st.caption(
    "Pending processed_pois grouped by spatial cluster. Use the buttons "
    "to manually resolve cases the auto-merge thresholds didn't decide."
)

eps = st.slider("Cluster radius (meters)", 10, 500, 55, step=5)
if st.button("Re-cluster"):
    st.cache_data.clear()

df = pending_clusters(eps_meters=eps)
if df.empty:
    st.success(
        "No pending clusters. Either everything has been merged or there are "
        "no pending records — try the Ingestion Jobs page."
    )
    st.stop()

cluster_ids = sorted(df["cluster_id"].unique().tolist())
st.info(
    f"{len(cluster_ids)} cluster(s) with 2+ members across {len(df)} records "
    f"(eps={eps}m)."
)

for cid in cluster_ids:
    members = df[df["cluster_id"] == cid].copy()
    head = members.iloc[0]
    title = (
        f"Cluster {int(cid)} — {len(members)} records · "
        f"~{head['lat']:.4f}, {head['lng']:.4f}"
    )
    with st.expander(title, expanded=False):
        st.dataframe(
            members[
                [
                    "id", "name_original", "address_normalized", "brand",
                    "phone_e164", "website_domain", "openooh_subcategory",
                    "lat", "lng", "quality_score",
                ]
            ],
            use_container_width=True,
            hide_index=True,
        )
        ids_in_cluster = members["id"].astype(int).tolist()
        picked = st.multiselect(
            "Select 2+ records to merge / reject",
            options=ids_in_cluster,
            default=ids_in_cluster,
            key=f"pick-{cid}",
        )
        col_a, col_b, _ = st.columns([1, 1, 4])
        if col_a.button("Merge selected", type="primary", key=f"merge-{cid}"):
            if len(picked) < 2:
                st.warning("Pick at least 2 records to merge.")
            else:
                try:
                    resp = post_json(
                        "/api/v1/admin/dedupe/manual-merge",
                        body={"processed_poi_ids": picked},
                    )
                    if resp.get("status") == "merged":
                        st.success(f"Merged → master_poi {resp.get('master_poi_id')}")
                    else:
                        st.warning(f"{resp.get('status')}: {resp.get('reason')}")
                    st.cache_data.clear()
                    st.rerun()
                except Exception as exc:  # noqa: BLE001
                    st.error(f"Merge failed: {exc}")

        if col_b.button("Reject selected", key=f"reject-{cid}"):
            if not picked:
                st.warning("Pick at least 1 record to reject.")
            else:
                try:
                    resp = post_json(
                        "/api/v1/admin/dedupe/manual-reject",
                        body={
                            "processed_poi_ids": picked,
                            "reason": f"manual reject from admin (cluster {int(cid)})",
                        },
                    )
                    st.success(f"Rejected {resp.get('rows_updated', 0)} record(s).")
                    st.cache_data.clear()
                    st.rerun()
                except Exception as exc:  # noqa: BLE001
                    st.error(f"Reject failed: {exc}")
