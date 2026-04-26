"""POI Explorer — Folium map of master_pois with filters."""

from __future__ import annotations

import folium
import streamlit as st
from streamlit_folium import st_folium

from admin.lib.queries import (
    admin_districts,
    admin_provinces,
    brands_with_counts,
    categories_with_counts,
    master_pois_for_map,
)

st.set_page_config(page_title="POI Explorer — POI Lake", layout="wide")
st.title("POI Explorer")

# ---- filters --------------------------------------------------------------

with st.sidebar:
    st.header("Filters")

    # Administrative unit filters take precedence over radius — easier to
    # think in terms of "Cầu Giấy district" than lat/lng.
    provs = admin_provinces()
    prov_label = st.selectbox(
        "Province",
        options=["(any)"] + [f"{r.code} — {r['name']}" for _, r in provs.iterrows()],
    )
    province_code = (
        None if prov_label == "(any)" else prov_label.split(" — ", 1)[0]
    )

    dist_options = ["(any)"]
    if province_code:
        dists = admin_districts(province_code)
        dist_options += [f"{r.code} — {r['name']}" for _, r in dists.iterrows()]
    dist_label = st.selectbox("District", options=dist_options)
    district_code = (
        None if dist_label == "(any)" else dist_label.split(" — ", 1)[0]
    )

    use_radius = st.checkbox(
        "Radius search (overrides admin filter for centring)",
        value=False,
    )
    if use_radius:
        lat = st.number_input("lat", value=21.0285, format="%.6f")
        lng = st.number_input("lng", value=105.8542, format="%.6f")
        radius_m = st.number_input("radius_m", 100, 50000, 5000, step=100)
    else:
        lat = lng = radius_m = None

    cats_df = categories_with_counts()
    cat_choice = st.selectbox(
        "Category",
        options=["(any)"] + cats_df["category"].astype(str).tolist(),
    )

    brand_df = brands_with_counts()
    brand_choice = st.selectbox(
        "Brand", options=["(any)"] + brand_df["brand"].astype(str).tolist()
    )

    min_conf = st.slider("Min confidence", 0.0, 1.0, 0.0, step=0.05)
    limit = st.slider("Max markers", 50, 2000, 500, step=50)

    if st.button("Refresh"):
        st.cache_data.clear()

# ---- query ----------------------------------------------------------------

df = master_pois_for_map(
    lat=lat, lng=lng, radius_m=int(radius_m) if radius_m else None,
    category=None if cat_choice == "(any)" else cat_choice,
    brand=None if brand_choice == "(any)" else brand_choice,
    province_code=province_code,
    district_code=district_code,
    min_confidence=min_conf,
    limit=limit,
)

st.caption(f"{len(df)} master records (cap {limit})")

if df.empty:
    st.info("No master_pois match the current filters.")
    st.stop()

# ---- map ------------------------------------------------------------------

center_lat = float(df["lat"].mean())
center_lng = float(df["lng"].mean())
fmap = folium.Map(location=[center_lat, center_lng], zoom_start=13, tiles="OpenStreetMap")

for _, row in df.iterrows():
    popup_html = (
        f"<b>{row['canonical_name']}</b><br>"
        f"brand: {row['brand'] or '—'}<br>"
        f"cat: {row['openooh_subcategory'] or row['openooh_category'] or '—'}<br>"
        f"sources: {row['sources_count']} · conf: {row['confidence']:.2f}<br>"
        f"<small>{row['canonical_address'] or ''}</small>"
    )
    color = "blue" if row["sources_count"] > 1 else "gray"
    folium.CircleMarker(
        location=[row["lat"], row["lng"]],
        radius=4 + min(int(row["sources_count"]) * 2, 10),
        color=color,
        fill=True,
        fill_opacity=0.7,
        popup=folium.Popup(popup_html, max_width=280),
        tooltip=row["canonical_name"],
    ).add_to(fmap)

st_folium(fmap, height=600, use_container_width=True, returned_objects=[])

st.divider()
st.subheader("Records")
st.dataframe(
    df.drop(columns=["openooh_category"]),
    use_container_width=True,
    hide_index=True,
)
