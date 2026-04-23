"""Idempotent seed runner.

Uses ``INSERT ... ON CONFLICT DO UPDATE`` so re-running the script reconciles
the DB to match the Python seed files (useful after editing a pattern).
"""

from __future__ import annotations

import logging

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from poi_lake.seeds.openooh_taxonomy import TAXONOMY
from poi_lake.seeds.sources import SOURCES
from poi_lake.seeds.vn_brands import BRANDS

logger = logging.getLogger(__name__)


async def seed_sources(session: AsyncSession) -> int:
    sql = text(
        """
        INSERT INTO sources (code, name, adapter_class, config, enabled, priority)
        VALUES (:code, :name, :adapter_class, CAST(:config AS JSONB), :enabled, :priority)
        ON CONFLICT (code) DO UPDATE SET
            name = EXCLUDED.name,
            adapter_class = EXCLUDED.adapter_class,
            config = EXCLUDED.config,
            priority = EXCLUDED.priority,
            updated_at = NOW()
        """
    )
    import json

    for row in SOURCES:
        await session.execute(
            sql,
            {
                "code": row["code"],
                "name": row["name"],
                "adapter_class": row["adapter_class"],
                "config": json.dumps(row["config"]),
                "enabled": row["enabled"],
                "priority": row["priority"],
            },
        )
    await session.commit()
    logger.info("Seeded %d sources", len(SOURCES))
    return len(SOURCES)


async def seed_openooh_categories(session: AsyncSession) -> int:
    # Two-pass: level-1 first (parent_code=NULL), then level-2, to satisfy FK.
    by_level: dict[int, list[dict[str, object]]] = {}
    for row in TAXONOMY:
        by_level.setdefault(row["level"], []).append(dict(row))

    sql = text(
        """
        INSERT INTO openooh_categories (code, name, parent_code, level)
        VALUES (:code, :name, :parent_code, :level)
        ON CONFLICT (code) DO UPDATE SET
            name = EXCLUDED.name,
            parent_code = EXCLUDED.parent_code,
            level = EXCLUDED.level
        """
    )
    total = 0
    for level in sorted(by_level.keys()):
        for row in by_level[level]:
            await session.execute(sql, row)
            total += 1
    await session.commit()
    logger.info("Seeded %d openooh_categories", total)
    return total


async def seed_brands(session: AsyncSession) -> int:
    sql = text(
        """
        INSERT INTO brands (name, aliases, category, parent_company, country, match_pattern, enabled)
        VALUES (:name, CAST(:aliases AS TEXT[]), :category, :parent_company, :country, :match_pattern, true)
        ON CONFLICT (name) DO UPDATE SET
            aliases = EXCLUDED.aliases,
            category = EXCLUDED.category,
            parent_company = EXCLUDED.parent_company,
            country = EXCLUDED.country,
            match_pattern = EXCLUDED.match_pattern
        """
    )
    for row in BRANDS:
        await session.execute(
            sql,
            {
                "name": row["name"],
                "aliases": row["aliases"],
                "category": row["category"],
                "parent_company": row["parent_company"],
                "country": row["country"],
                "match_pattern": row["match_pattern"],
            },
        )
    await session.commit()
    logger.info("Seeded %d brands", len(BRANDS))
    return len(BRANDS)


async def seed_all(session: AsyncSession) -> dict[str, int]:
    return {
        "sources": await seed_sources(session),
        "openooh_categories": await seed_openooh_categories(session),
        "brands": await seed_brands(session),
    }
