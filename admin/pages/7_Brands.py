"""Brands — group master_pois by brand, scoped by admin unit / category.

Two views:

  * **Summary table** — one row per brand: count of unique master records,
    average confidence, dominant category. Click a brand to drill down
    into the matching POIs on the existing POI Explorer (filter by brand).
  * **Top-brands chart** — bar chart of the largest 25 brands so you can
    eyeball coverage across the dataset.
"""

from __future__ import annotations

import streamlit as st

from admin.lib.queries import (
    admin_districts,
    admin_provinces,
    brand_summary,
    categories_with_counts,
)

st.set_page_config(page_title="Brands — POI Lake", layout="wide")
st.title("Brands")
st.caption(
    "Aggregate view of master POIs by brand. The brand label comes from "
    "the BrandDetector match against the seeded ``brands`` table — see the "
    "Sources page to add new brands."
)

# ---- filter row -----------------------------------------------------------

c1, c2, c3, c4, c5 = st.columns([2, 2, 2, 1, 1])

provs = admin_provinces()
prov_label = c1.selectbox(
    "Province",
    options=["(all)"] + [f"{r.code} — {r['name']}" for _, r in provs.iterrows()],
)
province_code = (
    None if prov_label == "(all)" else prov_label.split(" — ", 1)[0]
)

dist_options = ["(all)"]
if province_code:
    dists = admin_districts(province_code)
    dist_options += [f"{r.code} — {r['name']}" for _, r in dists.iterrows()]
dist_label = c2.selectbox("District", options=dist_options)
district_code = (
    None if dist_label == "(all)" else dist_label.split(" — ", 1)[0]
)

cats = categories_with_counts()
cat_choice = c3.selectbox(
    "Category", options=["(any)"] + cats["category"].astype(str).tolist()
)
min_conf = c4.slider("Min conf", 0.0, 1.0, 0.0, step=0.05)
if c5.button("Refresh"):
    st.cache_data.clear()

df = brand_summary(
    province_code=province_code,
    district_code=district_code,
    category=None if cat_choice == "(any)" else cat_choice,
    min_confidence=min_conf,
    limit=200,
)

if df.empty:
    st.info("No master POIs match the current filters yet.")
    st.stop()

# ---- KPIs -----------------------------------------------------------------

total_brands = len(df)
total_pois = int(df["n"].sum())
top1 = df.iloc[0]
c1, c2, c3 = st.columns(3)
c1.metric("Brands", f"{total_brands:,}")
c2.metric("Branded POIs", f"{total_pois:,}")
c3.metric("Largest", f"{top1['brand']}", delta=f"{int(top1['n'])} POIs")

st.divider()

# ---- Top-N bar chart ------------------------------------------------------

st.subheader("Top brands by POI count")
top_n = st.slider("Show top N", 5, min(50, total_brands), min(20, total_brands))
top = df.head(top_n).set_index("brand")["n"]
st.bar_chart(top)

st.divider()

# ---- Full table -----------------------------------------------------------

st.subheader("All matching brands")
display = df.copy()
display["avg_confidence"] = display["avg_confidence"].astype(float).round(2)
st.dataframe(
    display,
    use_container_width=True,
    column_config={
        "brand": "Brand",
        "n": st.column_config.NumberColumn("Count", format="%d"),
        "category": "Dominant category",
        "avg_confidence": st.column_config.NumberColumn(
            "Avg confidence", format="%.2f"
        ),
    },
    hide_index=True,
)

st.caption(
    "Click a brand on the POI Explorer's brand filter to see its POIs on "
    "the map."
)
