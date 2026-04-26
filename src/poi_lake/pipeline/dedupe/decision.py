"""Map a composite similarity score to a routing decision.

Three buckets per spec:
  * ``AUTO_MERGE`` — score ≥ 0.85 (default, configurable)
  * ``NEEDS_LLM`` — 0.65 ≤ score < 0.85
  * ``DISTINCT``  — score < 0.65
"""

from __future__ import annotations

from enum import StrEnum

from poi_lake.config import get_settings


class DedupeDecision(StrEnum):
    AUTO_MERGE = "auto_merge"
    NEEDS_LLM = "needs_llm"
    DISTINCT = "distinct"


def decide(
    composite_score: float,
    *,
    auto_threshold: float | None = None,
    llm_threshold: float | None = None,
) -> DedupeDecision:
    settings = get_settings()
    auto_t = auto_threshold if auto_threshold is not None else settings.dedupe_auto_merge_threshold
    llm_t = llm_threshold if llm_threshold is not None else settings.dedupe_llm_threshold

    if composite_score >= auto_t:
        return DedupeDecision.AUTO_MERGE
    if composite_score >= llm_t:
        return DedupeDecision.NEEDS_LLM
    return DedupeDecision.DISTINCT
