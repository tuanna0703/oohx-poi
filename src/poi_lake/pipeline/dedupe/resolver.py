"""LLM resolver for ambiguous pairs.

Used when ``decide(score) == NEEDS_LLM``. Sends the two records as JSON to
Claude Opus 4.7 and asks for a strict ``{"same": bool, "confidence": float,
"reason": str}`` answer.

Caches by a deterministic ``(min_id, max_id)`` Redis key with 7-day TTL so
the same pair is never resolved twice. (Cluster re-runs and worker retries
both hit this cache.)
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass
from typing import Any

import redis.asyncio as redis_asyncio

from poi_lake.config import get_settings
from poi_lake.observability.metrics import LLM_CALLS, LLM_TOKENS

logger = logging.getLogger(__name__)


_CACHE_TTL_SECONDS = 7 * 24 * 60 * 60
_CACHE_KEY_PREFIX = "poi-lake:dedupe:llm:"

_PROMPT_SYSTEM = (
    "You compare two POI records and decide if they describe the same physical "
    "place. Reply with a single compact JSON object on one line: "
    '{"same": true|false, "confidence": 0..1, "reason": "<short reason>"}. '
    "No markdown, no extra prose."
)

_PROMPT_TEMPLATE = """Are these two records the same physical location?

RECORD A:
{a_json}

RECORD B:
{b_json}

Consider: name, address, phone, website, brand, coordinates. Slight name
or address variations are common — focus on whether they could plausibly
be the same place."""


@dataclass(slots=True, frozen=True)
class LLMResolution:
    same: bool
    confidence: float
    reason: str
    cached: bool = False


class LLMResolver:
    """Wraps Anthropic's Claude API + Redis cache."""

    def __init__(
        self,
        *,
        redis_client: Any | None = None,
        anthropic_client: Any | None = None,
        model: str | None = None,
    ) -> None:
        settings = get_settings()
        self._model = model or settings.anthropic_model_resolver
        self._redis = redis_client
        self._anthropic = anthropic_client
        self._anthropic_key: str | None = (
            settings.anthropic_api_key.get_secret_value() if settings.anthropic_api_key else None
        )

    async def _get_redis(self):
        if self._redis is None:
            self._redis = redis_asyncio.from_url(
                get_settings().redis_url, encoding="utf-8", decode_responses=True
            )
        return self._redis

    def _get_anthropic(self):
        if self._anthropic is None:
            from anthropic import Anthropic

            if not self._anthropic_key:
                raise RuntimeError("ANTHROPIC_API_KEY not configured")
            self._anthropic = Anthropic(api_key=self._anthropic_key)
        return self._anthropic

    @staticmethod
    def _cache_key(record_a: dict, record_b: dict) -> str:
        # Order-independent — same pair (a,b) and (b,a) hit the same cache.
        a_id = record_a.get("id")
        b_id = record_b.get("id")
        if a_id is not None and b_id is not None:
            lo, hi = sorted([int(a_id), int(b_id)])
            return f"{_CACHE_KEY_PREFIX}{lo}:{hi}"
        # Fallback: hash the JSON content (no ids supplied).
        canon = json.dumps(
            sorted([record_a, record_b], key=lambda r: json.dumps(r, sort_keys=True)),
            sort_keys=True, ensure_ascii=False,
        )
        h = hashlib.sha256(canon.encode("utf-8")).hexdigest()[:16]
        return f"{_CACHE_KEY_PREFIX}h:{h}"

    async def resolve(self, record_a: dict, record_b: dict) -> LLMResolution:
        key = self._cache_key(record_a, record_b)
        rc = await self._get_redis()
        cached = await rc.get(key)
        if cached:
            try:
                data = json.loads(cached)
                LLM_CALLS.labels(self._model, "cached").inc()
                return LLMResolution(
                    same=bool(data["same"]),
                    confidence=float(data.get("confidence", 0.0)),
                    reason=str(data.get("reason", "")),
                    cached=True,
                )
            except (ValueError, KeyError):
                logger.warning("invalid cache value for %s — re-querying", key)

        try:
            result = await self._call_llm(record_a, record_b)
            LLM_CALLS.labels(self._model, "hit").inc()
        except Exception:
            LLM_CALLS.labels(self._model, "error").inc()
            raise
        await rc.set(
            key,
            json.dumps({"same": result.same, "confidence": result.confidence, "reason": result.reason}),
            ex=_CACHE_TTL_SECONDS,
        )
        return result

    async def _call_llm(self, record_a: dict, record_b: dict) -> LLMResolution:
        client = self._get_anthropic()
        body = _PROMPT_TEMPLATE.format(
            a_json=json.dumps(record_a, ensure_ascii=False, sort_keys=True),
            b_json=json.dumps(record_b, ensure_ascii=False, sort_keys=True),
        )

        # The Anthropic SDK is sync; run it in a thread so we don't block the
        # event loop. The dedupe worker only invokes a few of these per run.
        import asyncio

        def _do_call():
            return client.messages.create(
                model=self._model,
                max_tokens=200,
                system=_PROMPT_SYSTEM,
                messages=[{"role": "user", "content": body}],
            )

        msg = await asyncio.to_thread(_do_call)
        # Track token usage for cost dashboards.
        usage = getattr(msg, "usage", None)
        if usage is not None:
            LLM_TOKENS.labels(self._model, "input").inc(getattr(usage, "input_tokens", 0))
            LLM_TOKENS.labels(self._model, "output").inc(getattr(usage, "output_tokens", 0))
        text_blocks = [b.text for b in msg.content if getattr(b, "type", "") == "text"]
        raw = "".join(text_blocks).strip()
        return _parse_llm_reply(raw)


def _parse_llm_reply(raw: str) -> LLMResolution:
    """Lenient JSON extractor — strips ```json fences / surrounding noise."""
    s = raw.strip()
    if s.startswith("```"):
        s = s.strip("`").lstrip("json").strip()
    # Find the first { ... } block.
    start = s.find("{")
    end = s.rfind("}")
    if start == -1 or end == -1 or end < start:
        raise ValueError(f"no JSON object in LLM reply: {raw!r}")
    payload = json.loads(s[start : end + 1])
    return LLMResolution(
        same=bool(payload.get("same", False)),
        confidence=float(payload.get("confidence", 0.0)),
        reason=str(payload.get("reason", ""))[:500],
    )
