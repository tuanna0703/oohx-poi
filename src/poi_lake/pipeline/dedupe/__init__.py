"""Phase 4 — dedupe pipeline.

Pipeline order, applied to ``processed_pois`` with ``merge_status='pending'``:

  1. ``SpatialClusterer``      — PostGIS DBSCAN, 55m default eps.
  2. ``PairSimilarityScorer``  — pairwise score within each cluster.
  3. ``decide(score)``         — auto-merge / needs-llm / distinct.
  4. ``LLMResolver``           — Claude Opus 4.7 for ambiguous pairs (4b).
  5. ``MasterRecordBuilder``   — pick canonical values per merged cluster (4b).
  6. ``MergeService``          — INSERT/UPDATE master_pois + audit log (4b).
"""

from poi_lake.pipeline.dedupe.clusterer import SpatialClusterer
from poi_lake.pipeline.dedupe.decision import DedupeDecision, decide
from poi_lake.pipeline.dedupe.merge import MasterRecordBuilder, MergeService
from poi_lake.pipeline.dedupe.resolver import LLMResolution, LLMResolver
from poi_lake.pipeline.dedupe.similarity import PairScore, PairSimilarityScorer

__all__ = [
    "DedupeDecision",
    "LLMResolution",
    "LLMResolver",
    "MasterRecordBuilder",
    "MergeService",
    "PairScore",
    "PairSimilarityScorer",
    "SpatialClusterer",
    "decide",
]
