"""Which Anthropic failures latch the breaker, and which are just a bad minute.

Getting this wrong in either direction is expensive: latch on a rate limit and
dedupe stops for no reason; don't latch on an empty wallet and every pass
merges NEEDS_LLM pairs into separate masters that nothing ever revisits.
"""

from __future__ import annotations

import json

import anthropic
import httpx

from poi_lake.pipeline.dedupe.llm_state import (
    LLM_DISABLED_KEY,
    disable,
    enable,
    get_disabled,
    is_fatal_llm_error,
)


def _api_error(cls, message: str, status: int):
    """Build a real SDK exception — the constructor needs a response + body."""
    request = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    response = httpx.Response(status, request=request, json={"error": {"message": message}})
    return cls(message=message, response=response, body=None)


def test_no_credit_is_fatal() -> None:
    exc = _api_error(
        anthropic.BadRequestError,
        "Your credit balance is too low to access the Anthropic API.",
        400,
    )
    assert is_fatal_llm_error(exc) is True


def test_bad_key_is_fatal() -> None:
    assert is_fatal_llm_error(_api_error(anthropic.AuthenticationError, "invalid x-api-key", 401))
    assert is_fatal_llm_error(_api_error(anthropic.PermissionDeniedError, "forbidden", 403))


def test_rate_limit_and_overload_are_transient() -> None:
    assert is_fatal_llm_error(_api_error(anthropic.RateLimitError, "slow down", 429)) is False
    assert is_fatal_llm_error(_api_error(anthropic.InternalServerError, "boom", 500)) is False


def test_ordinary_400_is_not_fatal() -> None:
    """A malformed request is our bug, not an empty wallet — don't stop the world."""
    exc = _api_error(anthropic.BadRequestError, "max_tokens: must be >= 1", 400)
    assert is_fatal_llm_error(exc) is False


def test_non_anthropic_error_is_not_fatal() -> None:
    assert is_fatal_llm_error(RuntimeError("network hiccup")) is False


class _FakeRedis:
    """Minimal async stand-in — the breaker only needs get/set/delete."""

    def __init__(self) -> None:
        self.store: dict[str, str] = {}

    async def get(self, key: str) -> str | None:
        return self.store.get(key)

    async def set(self, key: str, value: str) -> None:
        self.store[key] = value

    async def delete(self, key: str) -> int:
        return 1 if self.store.pop(key, None) is not None else 0


async def test_disable_then_get_then_enable() -> None:
    rc = _FakeRedis()
    assert await get_disabled(rc) is None

    state = await disable("credit balance is too low", rc)
    assert state["reason"] == "credit balance is too low"
    assert state["since"]

    read_back = await get_disabled(rc)
    assert read_back == state

    assert await enable(rc) is True
    assert await get_disabled(rc) is None
    assert await enable(rc) is False, "clearing twice must be a no-op"


async def test_corrupt_flag_still_reports_paused() -> None:
    """A garbled value must not silently un-pause the pipeline."""
    rc = _FakeRedis()
    rc.store[LLM_DISABLED_KEY] = "not json"
    state = await get_disabled(rc)
    assert state is not None
    assert state["reason"] == "not json"


async def test_disable_truncates_a_huge_reason() -> None:
    rc = _FakeRedis()
    await disable("x" * 5000, rc)
    stored = json.loads(rc.store[LLM_DISABLED_KEY])
    assert len(stored["reason"]) == 500
