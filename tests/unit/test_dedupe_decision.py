"""Threshold routing for dedupe decisions."""

from __future__ import annotations

from poi_lake.pipeline.dedupe.decision import DedupeDecision, decide


def test_auto_merge_above_threshold() -> None:
    assert decide(0.95) is DedupeDecision.AUTO_MERGE
    assert decide(0.85) is DedupeDecision.AUTO_MERGE  # boundary inclusive


def test_needs_llm_in_middle_band() -> None:
    assert decide(0.84) is DedupeDecision.NEEDS_LLM
    assert decide(0.75) is DedupeDecision.NEEDS_LLM
    assert decide(0.65) is DedupeDecision.NEEDS_LLM  # boundary inclusive


def test_distinct_below_threshold() -> None:
    assert decide(0.64) is DedupeDecision.DISTINCT
    assert decide(0.30) is DedupeDecision.DISTINCT
    assert decide(0.0) is DedupeDecision.DISTINCT


def test_overrides_take_effect() -> None:
    # Tighter thresholds for a hot-fix scenario.
    assert decide(0.80, auto_threshold=0.95, llm_threshold=0.70) is DedupeDecision.NEEDS_LLM
    assert decide(0.96, auto_threshold=0.95, llm_threshold=0.70) is DedupeDecision.AUTO_MERGE
    assert decide(0.50, auto_threshold=0.95, llm_threshold=0.70) is DedupeDecision.DISTINCT
