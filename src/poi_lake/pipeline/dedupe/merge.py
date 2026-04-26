"""Cluster → master_pois merge logic.

For each spatial cluster of pending processed_pois we:

  1. Score every pair, classify with ``decide()``.
  2. Resolve NEEDS_LLM pairs (when a resolver is available).
  3. Build a union-find graph from confirmed-same edges.
  4. Each connected component → one ``master_pois`` row (or grow an existing
     one when a member already carries ``merged_into``).
  5. Audit-log every change to ``master_poi_history``.

Phase 4 v1 limitation: the clusterer pulls ``only_pending`` rows by default,
so a *new* batch of duplicates near an *already-merged* master won't attach
to that master automatically. ``MergeService.dedupe_pending`` exposes
``include_existing_masters=True`` for an opt-in pass that does. Default is
the cheap "process the new arrivals only" path.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from poi_lake.db.models import (
    MasterPOI,
    MasterPOIHistory,
    MasterPOIStatus,
    MergeStatus,
    ProcessedPOI,
    RawPOI,
    Source,
)
from poi_lake.observability import DEDUPE_DECISIONS, MERGE_MASTERS_CREATED
from poi_lake.observability.metrics import MERGE_MEMBERS
from poi_lake.pipeline.dedupe.clusterer import SpatialClusterer
from poi_lake.pipeline.dedupe.decision import DedupeDecision, decide
from poi_lake.pipeline.dedupe.resolver import LLMResolver, LLMResolution
from poi_lake.pipeline.dedupe.similarity import PairScore, PairSimilarityScorer

logger = logging.getLogger(__name__)


# --- canonical-field helpers ------------------------------------------------


def _best_by(rows: list[ProcessedPOI], key) -> ProcessedPOI | None:
    """Return the row maximizing ``key`` (skipping rows where key returns None)."""
    candidates = [(r, key(r)) for r in rows]
    candidates = [(r, v) for r, v in candidates if v is not None]
    if not candidates:
        return None
    return max(candidates, key=lambda t: t[1])[0]


def _quality(r: ProcessedPOI) -> float:
    return float(r.quality_score) if r.quality_score is not None else 0.0


def _addr_conf(r: ProcessedPOI) -> float:
    factors = r.quality_factors or {}
    return float(factors.get("address_confidence", 0.0))


def _weighted_centroid(rows: list[ProcessedPOI]) -> tuple[float, float]:
    """Quality-weighted centroid in (lat, lng). Falls back to plain average
    when all weights are zero. Reads coords via ST_Y / ST_X by raw SQL — the
    geography column doesn't expose them through the ORM."""
    # The caller fetched these rows already; we'd need a second query to pull
    # ST_X/ST_Y. Cleaner: store_location is needed only at write time, so we
    # delegate that to MergeService._make_master via raw SQL.
    raise NotImplementedError("centroid is computed in SQL by MergeService")


# --- master record builder --------------------------------------------------


class MasterRecordBuilder:
    """Pick canonical values from a list of ProcessedPOI rows."""

    def build(
        self,
        rows: list[ProcessedPOI],
        *,
        source_priority_by_id: dict[int, int] | None = None,
    ) -> dict[str, Any]:
        """Return a dict ready for INSERT into ``master_pois`` (location +
        canonical_name_embedding are written separately as raw SQL)."""
        if not rows:
            raise ValueError("no rows to build master from")

        priority = source_priority_by_id or {}

        # Per-field choice: highest quality_score, tie-break on source priority
        # (lower number = higher priority per spec).
        def picker(field: str, predicate=None):
            def key(r: ProcessedPOI):
                if predicate is not None and not predicate(r):
                    return None
                return (_quality(r), -priority.get(_source_id_of(r), 1000))
            return _best_by(rows, key)

        canonical_name_row = picker("name", lambda r: bool(r.name_original))
        canonical_addr_row = picker("address", lambda r: bool(r.address_normalized))
        canonical_phone_row = picker("phone", lambda r: bool(r.phone_e164))
        canonical_web_row = picker("website", lambda r: bool(r.website))
        canonical_cat_row = picker("category", lambda r: bool(r.openooh_category))
        canonical_brand_row = picker("brand", lambda r: bool(r.brand))

        # quality of the master = max quality of any contributing row.
        confidence = round(max(_quality(r) for r in rows), 3)

        return {
            "canonical_name": (canonical_name_row or rows[0]).name_original,
            "canonical_address": getattr(canonical_addr_row, "address_normalized", None),
            "canonical_address_components": getattr(
                canonical_addr_row, "address_components", None
            ),
            "canonical_phone": getattr(canonical_phone_row, "phone_e164", None),
            "canonical_website": getattr(canonical_web_row, "website", None),
            "openooh_category": getattr(canonical_cat_row, "openooh_category", None),
            "openooh_subcategory": getattr(canonical_cat_row, "openooh_subcategory", None),
            "brand": getattr(canonical_brand_row, "brand", None),
            "confidence": confidence,
            "quality_score": confidence,
            "_canonical_name_row": canonical_name_row or rows[0],  # passed to MergeService
        }


def _source_id_of(r: ProcessedPOI) -> int:
    # ProcessedPOI has raw_poi_id but not source_id directly; we look it up
    # via raw_pois lazily in the MergeService where we already have raws.
    return getattr(r, "_source_id", 0) or 0


# --- merge service ----------------------------------------------------------


class MergeService:
    """Top-level orchestrator. Run once per dedupe pass."""

    def __init__(self, resolver: LLMResolver | None = None) -> None:
        self.scorer = PairSimilarityScorer()
        self.resolver = resolver  # may be None (skips NEEDS_LLM pairs)
        self.builder = MasterRecordBuilder()

    async def merge_records(
        self,
        session: AsyncSession,
        processed_poi_ids: list[int],
    ) -> int | None:
        """Force-merge the given pending records into one master_pois row.

        Used by the admin UI's manual-override path. Skips any rows that are
        already merged or rejected. Returns the new master_poi.id, or None
        if nothing to merge.
        """
        if not processed_poi_ids:
            return None

        rows = (
            await session.execute(
                select(ProcessedPOI).where(
                    ProcessedPOI.id.in_(processed_poi_ids),
                    ProcessedPOI.merge_status == MergeStatus.PENDING.value,
                )
            )
        ).scalars().all()
        if not rows:
            return None

        # Attach source_id for the priority tie-breaker.
        raw_to_source = {
            rid: sid
            for rid, sid in (
                await session.execute(
                    select(RawPOI.id, RawPOI.source_id).where(
                        RawPOI.id.in_({r.raw_poi_id for r in rows})
                    )
                )
            ).all()
        }
        for r in rows:
            r._source_id = raw_to_source.get(r.raw_poi_id)  # type: ignore[attr-defined]

        src_rows = (await session.execute(select(Source.id, Source.priority))).all()
        priority_by_source = {sid: pri for sid, pri in src_rows}

        await self._make_master(session, rows, priority_by_source)
        await session.commit()

        # Read back the master_poi.id from the first row.
        await session.refresh(rows[0])
        return rows[0].merged_into

    async def dedupe_pending(
        self,
        session: AsyncSession,
        *,
        eps_meters: float | None = None,
    ) -> dict[str, int]:
        """Cluster + score + merge all currently-pending processed_pois.

        Returns counts: ``{clusters, masters_created, members_merged, llm_calls}``.
        """
        clusterer = SpatialClusterer()
        cluster_map = await clusterer.cluster(session, eps_meters=eps_meters, only_pending=True)
        groups = clusterer.group(cluster_map)

        stats = {"clusters": 0, "masters_created": 0, "members_merged": 0, "llm_calls": 0}
        if not groups:
            return stats

        # Pre-fetch source priorities (cheap, small table).
        src_rows = (await session.execute(select(Source.id, Source.priority))).all()
        priority_by_source = {sid: pri for sid, pri in src_rows}

        for cluster_id, member_ids in groups.items():
            stats["clusters"] += 1
            sub = await self._process_one_cluster(
                session, member_ids, priority_by_source
            )
            stats["masters_created"] += sub["masters_created"]
            stats["members_merged"] += sub["members_merged"]
            stats["llm_calls"] += sub["llm_calls"]

        await session.commit()
        return stats

    # -------------------------------------------------------------- internals

    async def _process_one_cluster(
        self,
        session: AsyncSession,
        member_ids: list[int],
        priority_by_source: dict[int, int],
    ) -> dict[str, int]:
        rows = (
            await session.execute(
                select(ProcessedPOI).where(ProcessedPOI.id.in_(member_ids))
            )
        ).scalars().all()
        if not rows:
            return {"masters_created": 0, "members_merged": 0, "llm_calls": 0}

        # Attach source_id to each row for the priority tie-breaker. Single
        # query against raw_pois.
        raw_to_source = {
            rid: sid
            for rid, sid in (
                await session.execute(
                    select(RawPOI.id, RawPOI.source_id).where(
                        RawPOI.id.in_({r.raw_poi_id for r in rows})
                    )
                )
            ).all()
        }
        for r in rows:
            r._source_id = raw_to_source.get(r.raw_poi_id)  # type: ignore[attr-defined]

        # Singleton fast path.
        if len(rows) == 1:
            await self._make_master(session, rows, priority_by_source)
            return {"masters_created": 1, "members_merged": 1, "llm_calls": 0}

        # Pairwise scoring + union-find.
        parent: dict[int, int] = {r.id: r.id for r in rows}

        def find(x: int) -> int:
            while parent[x] != x:
                parent[x] = parent[parent[x]]
                x = parent[x]
            return x

        def union(a: int, b: int) -> None:
            ra, rb = find(a), find(b)
            if ra != rb:
                parent[ra] = rb

        llm_calls = 0
        for i in range(len(rows)):
            for j in range(i + 1, len(rows)):
                a, b = rows[i], rows[j]
                score = self.scorer.score(a, b)
                d = decide(score.composite)
                DEDUPE_DECISIONS.labels(d.value).inc()
                if d is DedupeDecision.AUTO_MERGE:
                    union(a.id, b.id)
                elif d is DedupeDecision.NEEDS_LLM and self.resolver is not None:
                    llm_calls += 1
                    resolved = await self._resolve_pair(a, b)
                    if resolved.same and resolved.confidence >= 0.6:
                        union(a.id, b.id)

        # Build master per connected component.
        components: dict[int, list[ProcessedPOI]] = {}
        for r in rows:
            components.setdefault(find(r.id), []).append(r)

        masters_created = 0
        members_merged = 0
        for members in components.values():
            await self._make_master(session, members, priority_by_source)
            masters_created += 1
            members_merged += len(members)

        return {
            "masters_created": masters_created,
            "members_merged": members_merged,
            "llm_calls": llm_calls,
        }

    async def _resolve_pair(self, a: ProcessedPOI, b: ProcessedPOI) -> LLMResolution:
        a_dict = _serialize_for_llm(a)
        b_dict = _serialize_for_llm(b)
        return await self.resolver.resolve(a_dict, b_dict)  # type: ignore[union-attr]

    async def _make_master(
        self,
        session: AsyncSession,
        members: list[ProcessedPOI],
        priority_by_source: dict[int, int],
    ) -> None:
        """Insert a master_poi from ``members`` and link them all to it."""
        # Override _source_id priority lookups before building.
        for r in members:
            sid = getattr(r, "_source_id", 0)
            r._source_priority = priority_by_source.get(sid, 1000)  # type: ignore[attr-defined]

        canonical = self.builder.build(
            members, source_priority_by_id={getattr(r, "_source_id", 0): priority_by_source.get(getattr(r, "_source_id", 0), 1000) for r in members}
        )

        # Pull source_refs (we need raw_pois → source code).
        raw_ids = [r.raw_poi_id for r in members]
        raw_rows = (
            await session.execute(
                select(RawPOI.id, RawPOI.source_id, RawPOI.source_poi_id, Source.code)
                .join(Source, Source.id == RawPOI.source_id)
                .where(RawPOI.id.in_(raw_ids))
            )
        ).all()
        source_refs = [
            {"source": code, "source_poi_id": spid, "raw_poi_id": rid}
            for (rid, _sid, spid, code) in raw_rows
        ]

        canonical_name_row: ProcessedPOI = canonical.pop("_canonical_name_row")
        embedding = canonical_name_row.name_embedding

        # Quality-weighted centroid via SQL (geography → lat/lng extraction).
        # Build a VALUES list of (id, weight) to keep this O(n) and indexed.
        weights = {r.id: max(_quality(r), 0.01) for r in members}
        sum_w = sum(weights.values()) or 1.0
        centroid_sql = text(
            """
            SELECT
                SUM(ST_Y(location::geometry) * w) / :sum_w AS lat,
                SUM(ST_X(location::geometry) * w) / :sum_w AS lng
            FROM processed_pois p
            JOIN UNNEST(CAST(:ids AS BIGINT[]), CAST(:ws AS DOUBLE PRECISION[])) AS t(id, w)
                ON p.id = t.id
            """
        )
        ids_list = list(weights.keys())
        ws_list = [weights[i] for i in ids_list]
        centroid = (
            await session.execute(
                centroid_sql, {"ids": ids_list, "ws": ws_list, "sum_w": sum_w}
            )
        ).one()
        lat, lng = float(centroid[0]), float(centroid[1])

        # Admin codes: take the most common across members, falling back to
        # the canonical-name member's value (which itself was normalised
        # against admin_units bbox at ingest time).
        def _mode(values: list[str | None]) -> str | None:
            seen = [v for v in values if v]
            if not seen:
                return None
            from collections import Counter
            return Counter(seen).most_common(1)[0][0]

        province_code = _mode([m.province_code for m in members]) \
            or canonical_name_row.province_code
        district_code = _mode([m.district_code for m in members]) \
            or canonical_name_row.district_code
        ward_code = _mode([m.ward_code for m in members]) \
            or canonical_name_row.ward_code

        master_id_sql = text(
            """
            INSERT INTO master_pois (
                canonical_name, canonical_name_embedding,
                canonical_address, canonical_address_components,
                canonical_phone, canonical_website,
                location,
                openooh_category, openooh_subcategory, brand,
                province_code, district_code, ward_code,
                source_refs, merged_processed_ids,
                confidence, quality_score, status, version
            ) VALUES (
                :name, CAST(:emb AS VECTOR(384)),
                :addr, CAST(:addr_comp AS JSONB),
                :phone, :web,
                ST_GeogFromText(:wkt),
                :cat, :subcat, :brand,
                :prov, :dist, :ward,
                CAST(:srcrefs AS JSONB), CAST(:procids AS BIGINT[]),
                :conf, :q, :status, 1
            )
            RETURNING id
            """
        )
        import json as _json
        result = await session.execute(
            master_id_sql,
            {
                "name": canonical["canonical_name"],
                "emb": "[" + ",".join(f"{float(x):.7f}" for x in embedding) + "]",
                "addr": canonical["canonical_address"],
                "addr_comp": _json.dumps(canonical["canonical_address_components"])
                if canonical["canonical_address_components"]
                else None,
                "phone": canonical["canonical_phone"],
                "web": canonical["canonical_website"],
                "wkt": f"SRID=4326;POINT({lng} {lat})",
                "cat": canonical["openooh_category"],
                "subcat": canonical["openooh_subcategory"],
                "brand": canonical["brand"],
                "prov": province_code,
                "dist": district_code,
                "ward": ward_code,
                "srcrefs": _json.dumps(source_refs),
                "procids": [r.id for r in members],
                "conf": canonical["confidence"],
                "q": canonical["quality_score"],
                "status": MasterPOIStatus.ACTIVE.value,
            },
        )
        master_id = result.scalar_one()

        # Update processed_pois → merged_into + merge_status.
        proc_ids = [r.id for r in members]
        await session.execute(
            text(
                "UPDATE processed_pois SET merged_into = :mid, merge_status = 'merged' "
                "WHERE id = ANY(:ids)"
            ),
            {"mid": master_id, "ids": proc_ids},
        )

        # Audit log.
        await session.execute(
            text(
                """
                INSERT INTO master_poi_history (
                    master_poi_id, version, changed_fields,
                    previous_values, new_values, change_reason, changed_at
                ) VALUES (
                    :mid, 1, CAST(:cf AS JSONB),
                    CAST(:pv AS JSONB), CAST(:nv AS JSONB), :reason, NOW()
                )
                """
            ),
            {
                "mid": master_id,
                "cf": _json.dumps([
                    "canonical_name", "canonical_address", "canonical_phone",
                    "canonical_website", "brand", "openooh_category", "source_refs",
                ]),
                "pv": _json.dumps({}),  # creation
                "nv": _json.dumps(
                    {
                        "canonical_name": canonical["canonical_name"],
                        "brand": canonical["brand"],
                        "category": canonical["openooh_category"],
                        "subcategory": canonical["openooh_subcategory"],
                        "sources": [r["source"] for r in source_refs],
                        "processed_ids": [r.id for r in members],
                    }
                ),
                "reason": "initial_merge" if len(members) > 1 else "singleton_master",
            },
        )

        MERGE_MASTERS_CREATED.inc()
        MERGE_MEMBERS.inc(len(members))
        logger.info(
            "merge: master=%s members=%d sources=%s name=%r",
            master_id, len(members), {r["source"] for r in source_refs}, canonical["canonical_name"],
        )


def _serialize_for_llm(r: ProcessedPOI) -> dict:
    """Strip ProcessedPOI down to fields useful for LLM judgement."""
    return {
        "id": r.id,
        "name": r.name_original,
        "address": r.address_normalized,
        "address_components": r.address_components,
        "phone": r.phone_e164,
        "website": r.website,
        "website_domain": r.website_domain,
        "brand": r.brand,
        "category": r.openooh_category,
        "subcategory": r.openooh_subcategory,
    }
