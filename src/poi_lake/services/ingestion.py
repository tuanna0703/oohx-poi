"""IngestionService — load source, run adapter, persist raw_pois.

Job lifecycle:
    pending --> running --> completed | failed

Job types supported in Phase 2:
  * ``area_sweep``     params: {lat, lng, radius_m, category?}
  * ``category_search`` params: same as area_sweep, category required
  * ``detail_enrich``  params: {source_poi_ids: [...]}
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from poi_lake.adapters import (
    AdapterError,
    AdapterTransientError,
    RawPOIRecord,
    SourceAdapter,
    build_adapter_for_source,
)
from poi_lake.db.models import IngestionJob, IngestionJobStatus, RawPOI, Source
from poi_lake.observability import (
    INGEST_RAW_INSERTED,
)
from poi_lake.observability.metrics import INGEST_ERRORS, INGEST_RAW_DUPLICATES
from poi_lake.services.hashing import content_hash

logger = logging.getLogger(__name__)


class IngestionService:
    """Drives one ingestion job from start to finish."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def run_job(self, job_id: int) -> dict[str, int]:
        """Execute the job identified by ``job_id``.

        Returns the stats dict (also persisted on the job row).
        Raises only for unrecoverable bugs — adapter errors are recorded
        on the job row and re-raised as :class:`AdapterError` so workers
        can decide whether to retry.
        """
        job = await self._load_job(job_id)
        source = await self._load_source(job.source_id)
        if not source.enabled:
            await self._fail(job, f"source {source.code!r} is disabled")
            raise AdapterError(f"source {source.code!r} is disabled")

        await self._mark_running(job)
        adapter = build_adapter_for_source(source)
        stats = {"fetched": 0, "new": 0, "duplicate": 0, "errors": 0}
        # Normalize only once the rows are committed: a message sent before
        # the commit can reach a worker that does not see the row yet, and a
        # raw_poi it skips is never normalized. A failed job rolls them back.
        new_ids: list[int] = []

        try:
            async with adapter:
                async for record in self._iter_records(adapter, job):
                    stats["fetched"] += 1
                    try:
                        new_id = await self._insert_raw(
                            record, source.id, job.id, adapter.hash_ignore
                        )
                    except SQLAlchemyError as exc:
                        logger.exception("raw_pois insert failed: %s", exc)
                        stats["errors"] += 1
                        INGEST_ERRORS.labels(source.code).inc()
                        continue
                    if new_id is not None:
                        stats["new"] += 1
                        INGEST_RAW_INSERTED.labels(source.code).inc()
                        new_ids.append(new_id)
                    else:
                        stats["duplicate"] += 1
                        INGEST_RAW_DUPLICATES.labels(source.code).inc()
        except AdapterTransientError as exc:
            await self._fail(job, f"transient: {exc}", stats)
            raise
        except AdapterError as exc:
            await self._fail(job, str(exc), stats)
            raise
        except Exception as exc:
            await self._fail(job, f"unexpected: {exc!r}", stats)
            raise

        await self._mark_completed(job, stats)
        for new_id in new_ids:
            self._enqueue_normalize(new_id)
        return stats

    # ----------------------------------------------------------------- helpers

    async def _load_job(self, job_id: int) -> IngestionJob:
        job = await self.session.get(IngestionJob, job_id)
        if job is None:
            raise AdapterError(f"ingestion_job id={job_id} not found")
        return job

    async def _load_source(self, source_id: int) -> Source:
        source = await self.session.get(Source, source_id)
        if source is None:
            raise AdapterError(f"source id={source_id} not found")
        return source

    async def _iter_records(
        self, adapter: SourceAdapter, job: IngestionJob
    ):
        params = job.params or {}
        if job.job_type in ("area_sweep", "category_search"):
            try:
                lat = float(params["lat"])
                lng = float(params["lng"])
                radius_m = int(params["radius_m"])
            except (KeyError, TypeError, ValueError) as exc:
                raise AdapterError(f"missing/invalid area params: {exc}") from exc
            category = params.get("category")
            place = params.get("place")
            async for record in adapter.fetch_by_area(lat, lng, radius_m, category, place=place):
                yield record
            return

        if job.job_type == "detail_enrich":
            ids = params.get("source_poi_ids") or []
            for spid in ids:
                rec = await adapter.fetch_by_id(str(spid))
                if rec is not None:
                    yield rec
            return

        raise AdapterError(f"unsupported job_type {job.job_type!r}")

    async def _insert_raw(
        self,
        record: RawPOIRecord,
        source_id: int,
        job_id: int,
        hash_ignore: frozenset[str] = frozenset(),
    ) -> int | None:
        """Insert a raw_poi row, skipping if (source_id, source_poi_id, hash) exists.

        Returns the new ``raw_pois.id`` on insert; ``None`` on duplicate.
        """
        digest = content_hash(record.raw_payload, hash_ignore)
        location_wkt: str | None = None
        if record.location is not None:
            lat, lng = record.location
            # SRID 4326 longitude-then-latitude in WKT
            location_wkt = f"SRID=4326;POINT({lng} {lat})"

        stmt = (
            pg_insert(RawPOI)
            .values(
                source_id=source_id,
                source_poi_id=record.source_poi_id,
                raw_payload=record.raw_payload,
                content_hash=digest,
                location=location_wkt,
                ingestion_job_id=job_id,
            )
            .on_conflict_do_nothing(constraint="uq_raw_pois_source_id_hash")
            .returning(RawPOI.id)
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    @staticmethod
    def _enqueue_normalize(raw_poi_id: int) -> None:
        """Dispatch a Dramatiq message to normalize this raw_poi.

        Imported lazily so the IngestionService stays usable from contexts
        (CLI scripts, alembic) that haven't configured the broker yet.
        """
        try:
            from poi_lake.workers.normalize import run_normalize_raw_poi

            run_normalize_raw_poi.send(raw_poi_id)
        except Exception as exc:
            logger.warning(
                "could not enqueue normalize for raw_poi %d: %s — will be picked up by backfill",
                raw_poi_id, exc,
            )

    # ---------------------------------------------------------------- state

    async def _mark_running(self, job: IngestionJob) -> None:
        job.status = IngestionJobStatus.RUNNING.value
        job.started_at = datetime.now(UTC)
        await self.session.commit()

    async def _mark_completed(self, job: IngestionJob, stats: dict[str, int]) -> None:
        # Surface raw count so the crawl velocity endpoint can sum it.
        stats = {**stats, "raw_count": stats.get("new", 0)}
        job.status = IngestionJobStatus.COMPLETED.value
        job.completed_at = datetime.now(UTC)
        job.stats = stats
        await self.session.commit()
        await self._update_crawl_plan(job, success=True, raw_count=stats["raw_count"])
        await self._update_probe_cell(job, fetched=stats.get("fetched", 0))
        await self._split_province(job, fetched=stats.get("fetched", 0))

    async def _fail(
        self, job: IngestionJob, reason: str, stats: dict[str, int] | None = None
    ) -> None:
        # Roll back any pending insert state, then re-fetch the job in the
        # clean transaction and mutate via ORM. Direct attribute assignment
        # plus session.commit() is the path with the cleanest interaction
        # between AsyncSession + asyncpg + JSONB.
        await self.session.rollback()
        fresh = await self.session.get(IngestionJob, job.id)
        if fresh is None:
            return
        fresh.status = IngestionJobStatus.FAILED.value
        fresh.completed_at = datetime.now(UTC)
        fresh.error_message = reason[:2000]
        fresh.stats = stats or {}
        await self.session.commit()
        await self._update_crawl_plan(fresh, success=False, raw_count=0,
                                      error=reason[:500])
        await self._update_probe_cell(fresh, fetched=None)

    async def _update_probe_cell(self, job: IngestionJob, *, fetched: int | None) -> None:
        """Record a crawl planner probe's verdict on its cell.

        ``fetched=None`` is a failed attempt. The cell stays ``probing`` —
        dramatiq retries the job — and after three failed attempts it is
        treated as ``populated``: never write off a cell because the scraper
        hiccupped.
        """
        cell_id = (job.params or {}).get("probe_cell_id")
        if not cell_id:
            return
        if fetched is None:
            stmt = text(
                "UPDATE crawl_cells SET attempts = attempts + 1, "
                "status = CASE WHEN attempts + 1 >= 3 THEN 'populated' ELSE status END "
                "WHERE id = :id"
            )
            params: dict[str, Any] = {"id": int(cell_id)}
        else:
            stmt = text(
                "UPDATE crawl_cells SET attempts = attempts + 1, fetched = :f, "
                "probed_at = NOW(), "
                "status = CASE WHEN :f > 0 THEN 'populated' ELSE 'empty' END "
                "WHERE id = :id"
            )
            params = {"id": int(cell_id), "f": int(fetched)}
        try:
            await self.session.execute(stmt, params)
            await self.session.commit()
        except SQLAlchemyError as exc:
            logger.warning("probe cell update failed for cell %s: %s", cell_id, exc)
            await self.session.rollback()

    async def _split_province(self, job: IngestionJob, *, fetched: int) -> None:
        """A brand's province-wide search that came back full has more stores
        than one page shows: turn its row into a district sweep.

        The row restarts its counters (``pois_raw`` keeps what the province
        search found); a province without districts stays as it is.
        """
        from poi_lake.config import get_settings

        params = job.params or {}
        plan_id = params.get("crawl_plan_id")
        if params.get("sweep") != "province" or not plan_id:
            return
        if fetched < get_settings().crawl_brand_split_at:
            return
        try:
            await self.session.execute(
                text(
                    """
                    UPDATE crawl_plan cp SET
                      sweep = 'district', cells_total = d.n, cells_enqueued = 0,
                      cells_done = 0, cells_failed = 0,
                      status = 'in_progress', completed_at = NULL
                    FROM (
                        SELECT count(*) AS n FROM admin_units u
                        JOIN crawl_plan c ON u.parent_code = c.province_code
                        WHERE c.id = :pid AND u.level = 2 AND u.lng_min IS NOT NULL
                    ) d
                    WHERE cp.id = :pid AND cp.sweep = 'province' AND d.n > 0
                    """
                ),
                {"pid": int(plan_id)},
            )
            await self.session.commit()
        except SQLAlchemyError as exc:
            logger.warning("province split failed for plan %s: %s", plan_id, exc)
            await self.session.rollback()

    async def _update_crawl_plan(
        self,
        job: IngestionJob,
        *,
        success: bool,
        raw_count: int,
        error: str | None = None,
    ) -> None:
        """Tick the parent crawl_plan row, if any.

        Idempotent + concurrency-safe via a single SQL UPDATE that:
          * increments cells_done OR cells_failed
          * sums raw_count into pois_raw
          * flips status='done' once cells_done + cells_failed >= cells_total
            and the row is still in_progress
        """
        params = job.params or {}
        plan_id = params.get("crawl_plan_id")
        if not plan_id:
            return
        try:
            plan_id = int(plan_id)
        except (TypeError, ValueError):
            return

        # A grid job finishing after its row switched to district sweep must
        # not count as a district (the switch restarted the row's counters).
        sweep = params.get("sweep", "grid")
        if success:
            stmt = text(
                """
                UPDATE crawl_plan
                SET cells_done = cells_done + 1,
                    pois_raw   = pois_raw + :raw,
                    status     = CASE
                        WHEN status = 'in_progress'
                         AND cells_total IS NOT NULL
                         AND cells_done + 1 + cells_failed >= cells_total
                        THEN 'done'
                        ELSE status
                    END,
                    completed_at = CASE
                        WHEN status = 'in_progress'
                         AND cells_total IS NOT NULL
                         AND cells_done + 1 + cells_failed >= cells_total
                        THEN NOW()
                        ELSE completed_at
                    END
                WHERE id = :pid AND sweep = :sweep
                """
            )
            params_sql = {"raw": int(raw_count), "pid": plan_id, "sweep": sweep}
        else:
            stmt = text(
                """
                UPDATE crawl_plan
                SET cells_failed = cells_failed + 1,
                    error_summary = COALESCE(:err, error_summary),
                    status = CASE
                        WHEN status = 'in_progress'
                         AND cells_total IS NOT NULL
                         AND cells_done + cells_failed + 1 >= cells_total
                        THEN
                            CASE
                              WHEN cells_done = 0 THEN 'failed'
                              ELSE 'done'      -- partial success counts as done
                            END
                        ELSE status
                    END,
                    completed_at = CASE
                        WHEN status = 'in_progress'
                         AND cells_total IS NOT NULL
                         AND cells_done + cells_failed + 1 >= cells_total
                        THEN NOW()
                        ELSE completed_at
                    END
                WHERE id = :pid AND sweep = :sweep
                """
            )
            params_sql = {"err": error, "pid": plan_id, "sweep": sweep}
        try:
            await self.session.execute(stmt, params_sql)
            await self.session.commit()
        except SQLAlchemyError as exc:
            logger.warning("crawl_plan update failed for plan %d: %s", plan_id, exc)
            await self.session.rollback()
