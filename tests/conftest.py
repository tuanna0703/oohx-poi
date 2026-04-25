"""Shared pytest fixtures.

Integration tests require a live Postgres at the URL in DATABASE_URL — run
them inside the ``api`` container (``docker compose exec api pytest``) where
the URL points at the in-stack postgres service.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from poi_lake.db import get_engine, get_sessionmaker
from poi_lake.db.models import IngestionJob, RawPOI, Source


@pytest_asyncio.fixture
async def db_session() -> AsyncIterator[AsyncSession]:
    sm = get_sessionmaker()
    async with sm() as session:
        yield session


async def _purge_fake_source(session: AsyncSession, code: str) -> None:
    """Remove any leftover rows for this fake source code (orphan from prior runs)."""
    await session.execute(
        text(
            "DELETE FROM raw_pois WHERE source_id IN "
            "(SELECT id FROM sources WHERE code = :c)"
        ),
        {"c": code},
    )
    await session.execute(
        text(
            "DELETE FROM ingestion_jobs WHERE source_id IN "
            "(SELECT id FROM sources WHERE code = :c)"
        ),
        {"c": code},
    )
    await session.execute(text("DELETE FROM sources WHERE code = :c"), {"c": code})
    await session.commit()


@pytest_asyncio.fixture
async def fake_source() -> AsyncIterator[Source]:
    """Insert a fresh Source row pointing to FakeAdapter; clean up after.

    Uses an isolated session so teardown is independent of any test-body
    session state — important when a test session ends in an exception.
    """
    import uuid

    code = f"fake-test-{uuid.uuid4().hex[:8]}"
    sm = get_sessionmaker()
    async with sm() as setup:
        src = Source(
            code=code,
            name="Fake (test)",
            adapter_class="tests.fakes:FakeAdapter",
            config={},
            enabled=True,
            priority=999,
        )
        setup.add(src)
        await setup.commit()
        await setup.refresh(src)
        # Detach so the caller can use the entity without holding `setup` open.
        setup.expunge(src)
    yield src

    async with sm() as teardown:
        await _purge_fake_source(teardown, code)


@pytest_asyncio.fixture(scope="session", autouse=True)
async def _dispose_engine_at_session_end() -> AsyncIterator[None]:
    """Dispose the cached async engine after the whole test session.

    With ``asyncio_default_*_loop_scope = 'session'`` in pyproject.toml,
    every test shares one event loop, so the engine pool stays valid across
    tests. We only need to clean up at the very end.
    """
    yield
    eng = get_engine()
    await eng.dispose()
