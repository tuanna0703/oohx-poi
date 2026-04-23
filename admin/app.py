"""Streamlit admin UI placeholder.

Built out in Phase 6. For Phase 1 this just renders a status banner so the
``admin`` container has something to serve.
"""

import os

import streamlit as st

st.set_page_config(page_title="POI Lake Admin", layout="wide")

st.title("POI Lake — Admin")
st.caption(f"API URL: {os.getenv('POI_LAKE_API_URL', 'not set')}")

st.info(
    "Phase 1 foundation. Full admin UI (dashboard, POI explorer, dedupe queue, "
    "audit log) ships in Phase 6."
)
