"""Async SQLAlchemy engine, session, and declarative base.

ORM models will be added in later phases — Phase 1 only needs the engine up
so ``/health`` can ping the DB and Alembic can share the same URL.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from functools import lru_cache

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from poi_lake.config import get_settings


class Base(DeclarativeBase):
    """Declarative base for all ORM models."""


@lru_cache(maxsize=1)
def get_engine() -> AsyncEngine:
    settings = get_settings()
    return create_async_engine(
        settings.database_url,
        echo=settings.database_echo,
        pool_size=settings.database_pool_size,
        max_overflow=settings.database_max_overflow,
        # pool_pre_ping=True is intentionally OFF: asyncpg's connections raise
        # cleanly on broken sockets so we'd just be paying the ping cost on
        # every checkout. SQLAlchemy's sync ping path also fights with the
        # greenlet bridge under some test scenarios.
        pool_recycle=300,  # recycle every 5 min instead — cheap and effective
    )


@lru_cache(maxsize=1)
def get_sessionmaker() -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(
        bind=get_engine(),
        expire_on_commit=False,
        class_=AsyncSession,
    )


@asynccontextmanager
async def session_scope() -> AsyncIterator[AsyncSession]:
    """Context-managed session with commit-on-success / rollback-on-error."""
    session = get_sessionmaker()()
    try:
        yield session
        await session.commit()
    except Exception:
        await session.rollback()
        raise
    finally:
        await session.close()
