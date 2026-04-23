"""Smoke tests for Settings loading."""

from __future__ import annotations

import pytest

from poi_lake.config import Settings


def test_settings_loads_with_min_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_SECRET_KEY", "x" * 40)
    monkeypatch.setenv(
        "DATABASE_URL", "postgresql+asyncpg://poi:poi@localhost:5432/poi_lake"
    )
    s = Settings()  # type: ignore[call-arg]
    assert s.app_env == "development"
    assert s.app_port == 8000
    assert "asyncpg" in s.database_url
    assert "asyncpg" not in s.sync_database_url


def test_settings_rejects_short_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_SECRET_KEY", "tooshort")
    monkeypatch.setenv(
        "DATABASE_URL", "postgresql+asyncpg://poi:poi@localhost:5432/poi_lake"
    )
    with pytest.raises(ValueError, match="at least 32 characters"):
        Settings()  # type: ignore[call-arg]
