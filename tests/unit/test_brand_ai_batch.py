"""brand_ai.verify_many: verdicts asked several at a time, budget checked per
round, a failed candidate stops the run, every call logged."""

from __future__ import annotations

import threading
import time
from typing import Any

import pytest

from poi_lake.services import app_settings, brand_ai
from poi_lake.services.app_settings import DEFAULTS

pytestmark = pytest.mark.asyncio(loop_scope="function")


class _Session:
    def __init__(self) -> None:
        self.commits = 0

    async def commit(self) -> None:
        self.commits += 1


def _cands(n: int) -> list[dict[str, Any]]:
    return [{"key": f"k{i}", "name": f"Chain {i}", "stores": 20, "provinces": 5, "samples": []}
            for i in range(n)]


@pytest.fixture
def fake(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    state: dict[str, Any] = {"spent": 0.0, "logged": [], "now": 0, "peak": 0, "fail": set()}
    lock = threading.Lock()

    def call(key: str, model: str, prompt: str) -> tuple[dict[str, Any], int, int]:
        with lock:
            state["now"] += 1
            state["peak"] = max(state["peak"], state["now"])
        time.sleep(0.02)
        with lock:
            state["now"] -= 1
        name = prompt.splitlines()[0].removeprefix("Candidate: ")
        if name in state["fail"]:
            raise RuntimeError("down")
        return {"is_chain": True, "confidence": 0.9, "canonical_name": name}, 100, 20

    async def spent(_s: Any) -> float:
        return state["spent"]

    async def log(_s: Any, provider: str, model: str, purpose: str, t_in: int, t_out: int, ok: bool) -> None:
        state["logged"].append((provider, ok))
        state["spent"] += 1.0  # each logged call costs a dollar here

    monkeypatch.setitem(brand_ai.CALLERS, "claude", call)
    monkeypatch.setattr(brand_ai, "spent_this_month", spent)
    monkeypatch.setattr(brand_ai, "_log", log)
    monkeypatch.setattr(app_settings, "api_key", lambda _s, p: "k" if p == "claude" else None)
    return state


async def test_many_verdicts_come_back_several_at_a_time(fake: dict[str, Any]) -> None:
    s = _Session()
    settings = {**DEFAULTS, "budget_usd": 1000.0}
    verdicts, why = await brand_ai.verify_many(s, _cands(20), settings, concurrency=8)  # type: ignore[arg-type]
    assert why is None
    assert sorted(verdicts) == sorted(f"k{i}" for i in range(20))
    assert verdicts["k3"].canonical_name == "Chain 3"
    assert 1 < fake["peak"] <= 8
    assert len(fake["logged"]) == 20
    assert s.commits == 3  # rounds of 8, 8, 4


async def test_the_budget_stops_it_between_rounds(fake: dict[str, Any]) -> None:
    settings = {**DEFAULTS, "budget_usd": 10.0}
    verdicts, why = await brand_ai.verify_many(_Session(), _cands(30), settings, concurrency=4)  # type: ignore[arg-type]
    assert why == "budget spent"
    assert len(verdicts) == 12  # $0, $4, $8 spent before rounds 1-3; $12 stops round 4


async def test_a_candidate_no_provider_answers_stops_it(fake: dict[str, Any]) -> None:
    fake["fail"] = {"Chain 5"}
    settings = {**DEFAULTS, "budget_usd": 1000.0}
    verdicts, why = await brand_ai.verify_many(_Session(), _cands(20), settings, concurrency=4)  # type: ignore[arg-type]
    assert why is not None and why.startswith("Chain 5")
    assert len(verdicts) == 7  # rounds 1 and 2, less the failed one
    assert ("claude", False) in fake["logged"]
