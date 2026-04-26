"""Sync DB session for Streamlit.

Streamlit reruns the script top-to-bottom on every interaction, so the
async engine + session pattern from FastAPI doesn't fit. We use psycopg3
synchronously here.
"""

from __future__ import annotations

import os

import streamlit as st
from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker


def _sync_url() -> str:
    raw = os.getenv("DATABASE_URL", "")
    if not raw:
        raise RuntimeError("DATABASE_URL is not set in the admin container")
    # asyncpg → psycopg sync. asyncpg URLs look like
    # postgresql+asyncpg://user:pw@host:port/db; we want the bare or
    # +psycopg form.
    return raw.replace("+asyncpg", "+psycopg")


@st.cache_resource
def get_engine() -> Engine:
    return create_engine(_sync_url(), pool_pre_ping=True, future=True)


@st.cache_resource
def get_sessionmaker() -> sessionmaker[Session]:
    return sessionmaker(bind=get_engine(), expire_on_commit=False, class_=Session)


def session() -> Session:
    """Open a fresh sync session. Caller is responsible for closing
    (use ``with session() as s:``)."""
    return get_sessionmaker()()
