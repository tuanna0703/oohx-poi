"""Settings an admin changes from bizmap, kept in ``app_settings``.

One JSON document per key. ``brand_discovery`` holds the AI providers, the
auto-review thresholds and the brand lane; API keys inside it are encrypted
with a key derived from ``APP_SECRET_KEY`` and never leave this module in
clear except to call the provider.
"""

from __future__ import annotations

import base64
import hashlib
import json
from typing import Any

from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from poi_lake.config import get_settings

DISCOVERY = "brand_discovery"

PROVIDERS = ("claude", "openai")
ROLES = ("primary", "fallback", "off")

# What a fresh install runs with. Auto-add stays off until an admin turns it
# on: the first weeks are for checking the scores against real candidates.
DEFAULTS: dict[str, Any] = {
    "providers": {
        "claude": {"model": "claude-haiku-5-5", "role": "primary"},
        "openai": {"model": "", "role": "fallback"},
    },
    "budget_usd": 10.0,
    "auto_add": False,
    "add_at": 80,
    "reject_below": 50,
    "min_stores": 15,
    "min_provinces": 3,
    "brand_share": 0.30,
    "rescan_days": 90,
}


def _fernet() -> Fernet:
    secret = get_settings().app_secret_key.get_secret_value().encode()
    return Fernet(base64.urlsafe_b64encode(hashlib.sha256(b"poi-lake:settings:" + secret).digest()))


def seal(plain: str) -> str:
    return _fernet().encrypt(plain.encode()).decode()


def unseal(token: str) -> str | None:
    try:
        return _fernet().decrypt(token.encode()).decode()
    except (InvalidToken, ValueError):
        return None


async def read(session: AsyncSession, key: str) -> dict[str, Any]:
    row = (await session.execute(text("SELECT value FROM app_settings WHERE key = :k"), {"k": key})).first()
    value = row[0] if row else {}
    return value if isinstance(value, dict) else {}


async def write(session: AsyncSession, key: str, value: dict[str, Any]) -> None:
    await session.execute(
        text(
            """
            INSERT INTO app_settings (key, value, updated_at) VALUES (:k, CAST(:v AS jsonb), NOW())
            ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = NOW()
            """
        ),
        {"k": key, "v": json.dumps(value, ensure_ascii=False)},
    )


async def discovery(session: AsyncSession) -> dict[str, Any]:
    """The discovery settings with defaults filled in (keys still sealed)."""
    stored = await read(session, DISCOVERY)
    out = {**DEFAULTS, **{k: v for k, v in stored.items() if k != "providers"}}
    providers = {}
    for p in PROVIDERS:
        providers[p] = {**DEFAULTS["providers"][p], **(stored.get("providers", {}).get(p) or {})}
    out["providers"] = providers
    return out


def public(settings: dict[str, Any]) -> dict[str, Any]:
    """What bizmap may see: keys reduced to their last four characters."""
    out = {k: v for k, v in settings.items() if k != "providers"}
    out["providers"] = {
        p: {
            "model": cfg.get("model", ""),
            "role": cfg.get("role", "off"),
            "has_key": bool(cfg.get("key")),
            "key_last4": cfg.get("key_last4"),
            "checked": cfg.get("checked"),
        }
        for p, cfg in settings["providers"].items()
    }
    return out


def api_key(settings: dict[str, Any], provider: str) -> str | None:
    """The provider's key in clear: the stored one, else for Claude the
    ``ANTHROPIC_API_KEY`` the dedupe resolver already uses."""
    token = settings["providers"].get(provider, {}).get("key")
    if token:
        return unseal(token)
    if provider == "claude":
        env = get_settings().anthropic_api_key
        return env.get_secret_value() if env else None
    return None


async def brand_share(session: AsyncSession) -> float:
    """The brand lane's share: the admin's setting, else ``CRAWL_BRAND_SHARE``."""
    stored = await read(session, DISCOVERY)
    value = stored.get("brand_share")
    if isinstance(value, int | float) and 0 <= value <= 1:
        return float(value)
    return get_settings().crawl_brand_share
