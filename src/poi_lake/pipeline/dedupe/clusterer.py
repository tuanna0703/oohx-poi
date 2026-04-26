"""Spatial DBSCAN clustering of processed_pois.

Why ``ST_Transform(location, 3857)`` instead of degrees:

The original spec called for ``eps := 0.0005`` in degrees, but a degree of
longitude varies with latitude — ``0.0005°`` is ~55m at the equator and only
~52m at Hà Nội. EPSG:3857 (Web Mercator) is meters with ~3% distortion at
VN latitudes — well inside our DBSCAN tolerance, and lets the caller pass
``eps_meters`` honestly.

DBSCAN with ``minpoints=1`` makes every point its own cluster minimum; we
only use it to *partition* by spatial proximity, not to filter outliers.
The pairwise scorer is what filters real duplicates from coincidence.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from poi_lake.config import get_settings

logger = logging.getLogger(__name__)


class SpatialClusterer:
    """Returns ``{processed_poi_id: cluster_id}`` for a working set."""

    async def cluster(
        self,
        session: AsyncSession,
        *,
        eps_meters: float | None = None,
        min_points: int = 1,
        only_pending: bool = True,
        ids: Iterable[int] | None = None,
    ) -> dict[int, int]:
        """Run DBSCAN on a slice of ``processed_pois``.

        Parameters
        ----------
        eps_meters: cluster radius. Defaults to ``DEDUPE_CLUSTER_EPS_METERS``.
        min_points: DBSCAN core-point neighbour count. ``1`` means every
            point becomes a (possibly singleton) cluster — appropriate for
            partitioning, not outlier detection.
        only_pending: when True, restrict to ``merge_status = 'pending'``.
        ids: explicit set of processed_poi ids to cluster (overrides
            ``only_pending``). Useful for re-running on a slice.
        """
        eps = eps_meters if eps_meters is not None else get_settings().dedupe_cluster_eps_meters

        params: dict = {"eps": float(eps), "minp": int(min_points)}
        where_clauses = []
        if ids is not None:
            id_list = [int(i) for i in ids]
            if not id_list:
                return {}
            params["ids"] = id_list
            where_clauses.append("id = ANY(:ids)")
        elif only_pending:
            where_clauses.append("merge_status = 'pending'")
        # processed_pois.location is NOT NULL by schema, so no IS NULL guard.

        where_sql = (" WHERE " + " AND ".join(where_clauses)) if where_clauses else ""

        sql = text(
            f"""
            SELECT id, ST_ClusterDBSCAN(
                ST_Transform(location::geometry, 3857),
                eps := :eps,
                minpoints := :minp
            ) OVER () AS cluster_id
            FROM processed_pois
            {where_sql}
            """
        )
        rows = (await session.execute(sql, params)).all()
        out: dict[int, int] = {}
        for poi_id, cluster_id in rows:
            # min_points=1 means cluster_id is never NULL, but be defensive.
            if cluster_id is not None:
                out[int(poi_id)] = int(cluster_id)
        logger.info(
            "DBSCAN: %d points → %d clusters (eps=%.0fm)",
            len(rows), len(set(out.values())), eps,
        )
        return out

    @staticmethod
    def group(by_cluster: dict[int, int]) -> dict[int, list[int]]:
        """Invert ``{poi_id: cluster_id}`` → ``{cluster_id: [poi_ids]}``."""
        groups: dict[int, list[int]] = {}
        for poi_id, cluster_id in by_cluster.items():
            groups.setdefault(cluster_id, []).append(poi_id)
        return groups
