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
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select, text
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

        try:
            async with adapter:
                async for record in self._iter_records(adapter, job):
                    stats["fetched"] += 1
                    try:
                        new_id = await self._insert_raw(record, source.id, job.id)
                    except SQLAlchemyError as exc:
                        logger.exception("raw_pois insert failed: %s", exc)
                        stats["errors"] += 1
                        INGEST_ERRORS.labels(source.code).inc()
                        continue
                    if new_id is not None:
                        stats["new"] += 1
                        INGEST_RAW_INSERTED.labels(source.code).inc()
                        self._enqueue_normalize(new_id)
                    else:
                        stats["duplicate"] += 1
                        INGEST_RAW_DUPLICATES.labels(source.code).inc()
        except AdapterTransientError as exc:
            await self._fail(job, f"transient: {exc}", stats)
            raise
        except AdapterError as exc:
            await self._fail(job, str(exc), stats)
            raise
        except Exception as exc:  # noqa: BLE001
            await self._fail(job, f"unexpected: {exc!r}", stats)
            raise

        await self._mark_completed(job, stats)
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
            async for record in adapter.fetch_by_area(lat, lng, radius_m, category):
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
        self, record: RawPOIRecord, source_id: int, job_id: int
    ) -> int | None:
        """Insert a raw_poi row, skipping if (source_id, source_poi_id, hash) exists.

        Returns the new ``raw_pois.id`` on insert; ``None`` on duplicate.
        """
        digest = content_hash(record.raw_payload)
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
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "could not enqueue normalize for raw_poi %d: %s — will be picked up by backfill",
                raw_poi_id, exc,
            )

    # ---------------------------------------------------------------- state

    async def _mark_running(self, job: IngestionJob) -> None:
        job.status = IngestionJobStatus.RUNNING.value
        job.started_at = datetime.now(timezone.utc)
        await self.session.commit()

    async def _mark_completed(self, job: IngestionJob, stats: dict[str, int]) -> None:
        # Surface raw count so the crawl velocity endpoint can sum it.
        stats = {**stats, "raw_count": stats.get("new", 0)}
        job.status = IngestionJobStatus.COMPLETED.value
        job.completed_at = datetime.now(timezone.utc)
        job.stats = stats
        await self.session.commit()
        await self._update_crawl_plan(job, success=True, raw_count=stats["raw_count"])

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
        fresh.completed_at = datetime.now(timezone.utc)
        fresh.error_message = reason[:2000]
        fresh.stats = stats or {}
        await self.session.commit()
        await self._update_crawl_plan(fresh, success=False, raw_count=0,
                                      error=reason[:500])

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
                WHERE id = :pid
                """
            )
            params_sql = {"raw": int(raw_count), "pid": plan_id}
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
                WHERE id = :pid
                """
            )
            params_sql = {"err": error, "pid": plan_id}
        try:
            await self.session.execute(stmt, params_sql)
            await self.session.commit()
        except SQLAlchemyError as exc:
            logger.warning("crawl_plan update failed for plan %d: %s", plan_id, exc)
            await self.session.rollback()
