"""Ask an AI whether a brand candidate is a real chain.

The candidate's name, its sample store names and their categories go to the
primary provider; on an error, or when the month's budget is spent, to the
fallback. Every call is logged in ``ai_usage`` with its cost, so the monthly
budget (``budget_usd`` in the discovery settings) can be enforced.

Claude goes through the Anthropic SDK; ChatGPT through OpenAI's REST API with
httpx (no OpenAI SDK in the image). An OpenAI call's cost is logged as
unknown: its prices are not in this table, so it does not count against the
budget.
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass
from typing import Any

import httpx
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from poi_lake.services import app_settings

logger = logging.getLogger(__name__)

# USD per million tokens (input, output).
CLAUDE_PRICES: dict[str, tuple[float, float]] = {
    "claude-haiku-5-5": (0.10, 0.50),
    "claude-sonnet-5-5": (2.00, 10.00),
    "claude-opus-5-5": (4.00, 20.00),
    "claude-haiku-4-5": (1.00, 5.00),
}

CATEGORIES = (
    "retail", "hospitality", "financial", "health_and_beauty", "point_of_care",
    "education", "entertainment", "sports_and_fitness", "travel", "transit", "leisure",
    "office_buildings", "residential", "industrial", "government",
)

VERDICT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "is_chain": {"type": "boolean"},
        "confidence": {"type": "number"},
        "canonical_name": {"type": "string"},
        "category": {"type": "string", "enum": list(CATEGORIES)},
        "parent_company": {"type": ["string", "null"]},
        "aliases": {"type": "array", "items": {"type": "string"}},
        "search_term": {"type": "string"},
        "note": {"type": "string"},
    },
    "required": ["is_chain", "confidence", "canonical_name", "category", "parent_company",
                 "aliases", "search_term", "note"],
    "additionalProperties": False,
}

SYSTEM = (
    "You check candidate retail and service chains found in a Vietnamese map dataset. "
    "Given a candidate name and sample place names that matched it, decide whether the "
    "candidate is one real chain or brand operating in Vietnam (several outlets under one "
    "brand), as opposed to a common word, a place name, a generic shop type or unrelated "
    "shops that share a word. Reply with the JSON object only. canonical_name: the brand as "
    "it writes itself. aliases: other spellings seen in the samples or commonly used, at "
    "most 5. search_term: what to type into Google Maps to find its outlets, at most 40 "
    "characters. note: one short sentence in Vietnamese for an admin, saying what the "
    "brand is and anything that could cause false matches."
)


@dataclass(slots=True)
class Verdict:
    provider: str
    model: str
    is_chain: bool
    confidence: float
    canonical_name: str
    category: str
    parent_company: str | None
    aliases: list[str]
    search_term: str
    note: str

    def as_json(self) -> dict[str, Any]:
        return {
            "provider": self.provider, "model": self.model, "is_chain": self.is_chain,
            "confidence": self.confidence, "canonical_name": self.canonical_name,
            "category": self.category, "parent_company": self.parent_company,
            "aliases": self.aliases, "search_term": self.search_term, "note": self.note,
        }


class AiUnavailableError(Exception):
    """No provider configured, every provider failed, or the budget is spent."""


def prompt_for(candidate: dict[str, Any]) -> str:
    lines = [f"Candidate: {candidate['name']}",
             f"Stores matched: {candidate['stores']} in {candidate['provinces']} provinces",
             "Sample place names (category):"]
    lines += [f"- {s['name']} ({s.get('category') or 'unknown'})" for s in candidate["samples"][:12]]
    return "\n".join(lines)


def parse(raw: str) -> dict[str, Any]:
    s = raw.strip()
    if s.startswith("```"):
        s = s.strip("`").removeprefix("json").strip()
    start, end = s.find("{"), s.rfind("}")
    data = json.loads(s[start: end + 1])
    if not isinstance(data, dict):
        raise ValueError("not an object")
    return data


def _verdict(provider: str, model: str, data: dict[str, Any], fallback_name: str) -> Verdict:
    category = data.get("category")
    return Verdict(
        provider=provider,
        model=model,
        is_chain=bool(data.get("is_chain")),
        confidence=max(0.0, min(1.0, float(data.get("confidence") or 0))),
        canonical_name=str(data.get("canonical_name") or fallback_name)[:100],
        category=category if category in CATEGORIES else "retail",
        parent_company=(str(data["parent_company"])[:200] if data.get("parent_company") else None),
        aliases=[str(a)[:100] for a in (data.get("aliases") or []) if str(a).strip()][:5],
        search_term=str(data.get("search_term") or fallback_name)[:40],
        note=str(data.get("note") or "")[:400],
    )


def _call_claude(key: str, model: str, prompt: str) -> tuple[dict[str, Any], int, int]:
    from anthropic import Anthropic

    client = Anthropic(api_key=key, timeout=60.0)
    response = client.messages.create(
        model=model,
        max_tokens=2000,
        system=SYSTEM,
        output_config={"effort": "low", "format": {"type": "json_schema", "schema": VERDICT_SCHEMA}},
        messages=[{"role": "user", "content": prompt}],
    )
    if response.stop_reason == "refusal":
        raise RuntimeError("refused")
    text_out = next((b.text for b in response.content if b.type == "text"), "")
    usage = response.usage
    return parse(text_out), int(usage.input_tokens or 0), int(usage.output_tokens or 0)


def _call_openai(key: str, model: str, prompt: str) -> tuple[dict[str, Any], int, int]:
    response = httpx.post(
        "https://api.openai.com/v1/chat/completions",
        headers={"Authorization": f"Bearer {key}"},
        json={
            "model": model,
            "messages": [{"role": "system", "content": SYSTEM}, {"role": "user", "content": prompt}],
            "response_format": {
                "type": "json_schema",
                "json_schema": {"name": "verdict", "schema": VERDICT_SCHEMA, "strict": True},
            },
        },
        timeout=60.0,
    )
    response.raise_for_status()
    body = response.json()
    usage = body.get("usage") or {}
    return (
        parse(body["choices"][0]["message"]["content"]),
        int(usage.get("prompt_tokens") or 0),
        int(usage.get("completion_tokens") or 0),
    )


CALLERS = {"claude": _call_claude, "openai": _call_openai}


def cost(provider: str, model: str, tokens_in: int, tokens_out: int) -> float | None:
    if provider != "claude" or model not in CLAUDE_PRICES:
        return None
    p_in, p_out = CLAUDE_PRICES[model]
    return (tokens_in * p_in + tokens_out * p_out) / 1_000_000


async def spent_this_month(session: AsyncSession) -> float:
    return float(
        (
            await session.execute(
                text(
                    "SELECT COALESCE(SUM(cost_usd), 0) FROM ai_usage "
                    "WHERE at >= date_trunc('month', NOW() AT TIME ZONE 'Asia/Ho_Chi_Minh') "
                    "AT TIME ZONE 'Asia/Ho_Chi_Minh'"
                )
            )
        ).scalar_one()
    )


async def _log(session: AsyncSession, provider: str, model: str, purpose: str,
               tokens_in: int, tokens_out: int, ok: bool) -> None:
    await session.execute(
        text(
            "INSERT INTO ai_usage (provider, model, purpose, input_tokens, output_tokens, cost_usd, ok) "
            "VALUES (:p, :m, :u, :i, :o, :c, :ok)"
        ),
        {"p": provider, "m": model, "u": purpose, "i": tokens_in, "o": tokens_out,
         "c": cost(provider, model, tokens_in, tokens_out), "ok": ok},
    )


def order(settings: dict[str, Any]) -> list[str]:
    """Providers to try, primary first; those turned off or without a model left out."""
    rank = {"primary": 0, "fallback": 1}
    usable = [p for p, cfg in settings["providers"].items()
              if cfg.get("role") in rank and cfg.get("model")]
    return sorted(usable, key=lambda p: rank[settings["providers"][p]["role"]])


async def verify(session: AsyncSession, candidate: dict[str, Any],
                 settings: dict[str, Any] | None = None) -> Verdict:
    """The first provider's verdict that comes back; raises AiUnavailableError."""
    settings = settings or await app_settings.discovery(session)
    if await spent_this_month(session) >= float(settings["budget_usd"]):
        raise AiUnavailableError("budget spent")
    prompt = prompt_for(candidate)
    errors = []
    for provider in order(settings):
        key = app_settings.api_key(settings, provider)
        model = settings["providers"][provider]["model"]
        if not key:
            continue
        try:
            data, t_in, t_out = await asyncio.to_thread(CALLERS[provider], key, model, prompt)
        except Exception as exc:  # any provider failure moves on to the next one
            logger.warning("brand AI %s failed: %s", provider, type(exc).__name__)
            await _log(session, provider, model, "brand_verify", 0, 0, False)
            errors.append(f"{provider}: {type(exc).__name__}")
            continue
        await _log(session, provider, model, "brand_verify", t_in, t_out, True)
        return _verdict(provider, model, data, candidate["name"])
    raise AiUnavailableError("; ".join(errors) or "no provider configured")


async def check(session: AsyncSession, provider: str) -> dict[str, Any]:
    """A tiny call to prove the key and model work. Never returns the key."""
    settings = await app_settings.discovery(session)
    key = app_settings.api_key(settings, provider)
    model = settings["providers"][provider]["model"]
    if not key or not model:
        return {"ok": False, "error": "Chưa có khoá hoặc chưa chọn model"}
    sample = {"name": "Circle K", "stores": 400, "provinces": 20,
              "samples": [{"name": "Circle K Lê Lợi", "category": "retail.convenience_stores"}]}
    try:
        data, t_in, t_out = await asyncio.to_thread(CALLERS[provider], key, model, prompt_for(sample))
    except httpx.HTTPStatusError as exc:
        await _log(session, provider, model, "check", 0, 0, False)
        return {"ok": False, "error": f"HTTP {exc.response.status_code}"}
    except Exception as exc:
        await _log(session, provider, model, "check", 0, 0, False)
        return {"ok": False, "error": type(exc).__name__}
    await _log(session, provider, model, "check", t_in, t_out, True)
    return {"ok": True, "is_chain": bool(data.get("is_chain"))}
