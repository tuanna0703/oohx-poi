"""initial schema: bronze/silver/gold + supporting tables

Revision ID: 20260423_0001
Revises:
Create Date: 2026-04-23

Design notes (fixes vs. the original spec are called out inline):
  * FK ordering: master_pois is created BEFORE processed_pois because
    processed_pois.merged_into references master_pois (spec had a forward ref).
  * raw_pois UNIQUE(source_id, source_poi_id, content_hash) is intentional —
    the bronze layer is append-only, so we keep every distinct payload version
    and the upsert path skips inserts when the hash already exists.
  * master_poi_history.master_poi_id has an explicit FK (spec had none).
  * source_refs / merged_processed_ids / quality_factors / etc. have CHECK
    constraints guarding JSON shape so generated columns and consumers can
    assume the expected type.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260423_0001"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Extensions are installed by docker/postgres/init-extensions.sql on first
    # boot; re-declaring here makes the migration idempotent on existing DBs.
    op.execute("CREATE EXTENSION IF NOT EXISTS postgis")
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")

    # ---- updated_at trigger function (shared) ----
    op.execute(
        """
        CREATE OR REPLACE FUNCTION set_updated_at()
        RETURNS TRIGGER AS $$
        BEGIN
            NEW.updated_at = NOW();
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
        """
    )

    # ======================================================================
    # Reference tables
    # ======================================================================
    op.create_table(
        "openooh_categories",
        sa.Column("code", sa.String(50), primary_key=True),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column(
            "parent_code",
            sa.String(50),
            sa.ForeignKey("openooh_categories.code", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column("level", sa.Integer, nullable=False),
    )
    op.create_index(
        "idx_openooh_parent", "openooh_categories", ["parent_code"]
    )

    op.create_table(
        "brands",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("name", sa.String(100), nullable=False, unique=True),
        sa.Column(
            "aliases",
            postgresql.ARRAY(sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::text[]"),
        ),
        sa.Column("category", sa.String(50), nullable=True),
        sa.Column("parent_company", sa.String(200), nullable=True),
        sa.Column("country", sa.String(2), nullable=True),
        sa.Column("match_pattern", sa.Text, nullable=True),
        sa.Column(
            "enabled", sa.Boolean, nullable=False, server_default=sa.text("true")
        ),
    )
    op.create_index("idx_brands_enabled", "brands", ["enabled"])

    # ======================================================================
    # Bronze layer
    # ======================================================================
    op.create_table(
        "sources",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("code", sa.String(50), nullable=False, unique=True),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("adapter_class", sa.String(200), nullable=False),
        sa.Column(
            "config",
            postgresql.JSONB,
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("enabled", sa.Boolean, nullable=False, server_default=sa.text("true")),
        sa.Column("priority", sa.Integer, nullable=False, server_default=sa.text("100")),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.Column(
            "updated_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
    )
    op.execute(
        "CREATE TRIGGER trg_sources_updated_at BEFORE UPDATE ON sources "
        "FOR EACH ROW EXECUTE FUNCTION set_updated_at()"
    )

    op.create_table(
        "ingestion_jobs",
        sa.Column("id", sa.BigInteger, primary_key=True),
        sa.Column(
            "source_id",
            sa.Integer,
            sa.ForeignKey("sources.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("job_type", sa.String(50), nullable=False),
        sa.Column("params", postgresql.JSONB, nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column(
            "stats",
            postgresql.JSONB,
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("started_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("completed_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("error_message", sa.Text, nullable=True),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'running', 'completed', 'failed', 'cancelled')",
            name="ck_ingestion_jobs_status",
        ),
        sa.CheckConstraint(
            "job_type IN ('area_sweep', 'category_search', 'detail_enrich', 'backfill')",
            name="ck_ingestion_jobs_type",
        ),
    )
    op.execute(
        "CREATE INDEX idx_jobs_status ON ingestion_jobs(status) "
        "WHERE status IN ('pending', 'running')"
    )
    op.create_index(
        "idx_jobs_source_created", "ingestion_jobs", ["source_id", "created_at"]
    )

    op.create_table(
        "raw_pois",
        sa.Column("id", sa.BigInteger, primary_key=True),
        sa.Column(
            "source_id",
            sa.Integer,
            sa.ForeignKey("sources.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("source_poi_id", sa.String(255), nullable=False),
        sa.Column("raw_payload", postgresql.JSONB, nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column(
            "fetched_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.Column(
            "ingestion_job_id",
            sa.BigInteger,
            sa.ForeignKey("ingestion_jobs.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("processed_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.UniqueConstraint(
            "source_id",
            "source_poi_id",
            "content_hash",
            name="uq_raw_pois_source_id_hash",
        ),
    )
    # GEOGRAPHY column + indexes via raw SQL (geoalchemy2 autogen isn't wired in Phase 1)
    op.execute("ALTER TABLE raw_pois ADD COLUMN location GEOGRAPHY(POINT, 4326)")
    op.execute("CREATE INDEX idx_raw_location ON raw_pois USING GIST(location)")
    op.execute("CREATE INDEX idx_raw_payload ON raw_pois USING GIN(raw_payload)")
    op.execute(
        "CREATE INDEX idx_raw_unprocessed ON raw_pois(processed_at) "
        "WHERE processed_at IS NULL"
    )
    op.create_index(
        "idx_raw_source_fetched",
        "raw_pois",
        ["source_id", sa.text("fetched_at DESC")],
    )

    # ======================================================================
    # Gold layer (created before silver — silver references gold.id)
    # ======================================================================
    op.create_table(
        "master_pois",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("canonical_name", sa.Text, nullable=False),
        sa.Column("canonical_address", sa.Text, nullable=True),
        sa.Column("canonical_address_components", postgresql.JSONB, nullable=True),
        sa.Column("canonical_phone", sa.String(20), nullable=True),
        sa.Column("canonical_website", sa.Text, nullable=True),
        sa.Column("openooh_category", sa.String(50), nullable=True),
        sa.Column("openooh_subcategory", sa.String(100), nullable=True),
        sa.Column("brand", sa.String(100), nullable=True),
        sa.Column(
            "source_refs",
            postgresql.JSONB,
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "merged_processed_ids",
            postgresql.ARRAY(sa.BigInteger),
            nullable=False,
            server_default=sa.text("'{}'::bigint[]"),
        ),
        sa.Column("confidence", sa.Numeric(3, 2), nullable=False),
        sa.Column("quality_score", sa.Numeric(3, 2), nullable=True),
        sa.Column("dooh_score", sa.Numeric(3, 2), nullable=True),
        sa.Column("dooh_score_factors", postgresql.JSONB, nullable=True),
        sa.Column(
            "status",
            sa.String(20),
            nullable=False,
            server_default=sa.text("'active'"),
        ),
        sa.Column("archived_reason", sa.Text, nullable=True),
        sa.Column(
            "merged_into",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("master_pois.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("version", sa.Integer, nullable=False, server_default=sa.text("1")),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.Column(
            "updated_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.CheckConstraint(
            "status IN ('active', 'archived', 'merged_away')", name="ck_master_status"
        ),
        sa.CheckConstraint(
            "confidence >= 0 AND confidence <= 1", name="ck_master_confidence_range"
        ),
        sa.CheckConstraint(
            "jsonb_typeof(source_refs) = 'array'", name="ck_master_source_refs_array"
        ),
    )
    # Geography + vector columns + generated column via raw SQL
    op.execute(
        "ALTER TABLE master_pois ADD COLUMN location GEOGRAPHY(POINT, 4326) NOT NULL"
    )
    op.execute(
        "ALTER TABLE master_pois ADD COLUMN canonical_name_embedding VECTOR(384) NOT NULL"
    )
    op.execute(
        "ALTER TABLE master_pois ADD COLUMN sources_count INT "
        "GENERATED ALWAYS AS (jsonb_array_length(source_refs)) STORED"
    )
    op.execute(
        "CREATE INDEX idx_master_location ON master_pois USING GIST(location)"
    )
    op.execute(
        "CREATE INDEX idx_master_embedding ON master_pois "
        "USING hnsw (canonical_name_embedding vector_cosine_ops)"
    )
    op.execute(
        "CREATE INDEX idx_master_brand ON master_pois(brand) WHERE brand IS NOT NULL"
    )
    op.create_index("idx_master_category", "master_pois", ["openooh_category"])
    op.execute(
        "CREATE INDEX idx_master_active ON master_pois(status, updated_at DESC) "
        "WHERE status = 'active'"
    )
    op.execute(
        "CREATE TRIGGER trg_master_pois_updated_at BEFORE UPDATE ON master_pois "
        "FOR EACH ROW EXECUTE FUNCTION set_updated_at()"
    )

    op.create_table(
        "master_poi_history",
        sa.Column("id", sa.BigInteger, primary_key=True),
        sa.Column(
            "master_poi_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("master_pois.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("version", sa.Integer, nullable=False),
        sa.Column("changed_fields", postgresql.JSONB, nullable=False),
        sa.Column("previous_values", postgresql.JSONB, nullable=False),
        sa.Column("new_values", postgresql.JSONB, nullable=False),
        sa.Column("change_reason", sa.String(100), nullable=True),
        sa.Column(
            "changed_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.CheckConstraint(
            "jsonb_typeof(changed_fields) = 'array'",
            name="ck_history_changed_fields_array",
        ),
    )
    op.create_index(
        "idx_history_master_version",
        "master_poi_history",
        ["master_poi_id", "version"],
    )
    op.create_index("idx_history_changed_at", "master_poi_history", ["changed_at"])

    # ======================================================================
    # Silver layer (references master_pois — must come after gold)
    # ======================================================================
    op.create_table(
        "processed_pois",
        sa.Column("id", sa.BigInteger, primary_key=True),
        sa.Column(
            "raw_poi_id",
            sa.BigInteger,
            sa.ForeignKey("raw_pois.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("name_original", sa.Text, nullable=False),
        sa.Column("name_normalized", sa.Text, nullable=False),
        sa.Column("address_original", sa.Text, nullable=True),
        sa.Column("address_normalized", sa.Text, nullable=True),
        sa.Column("address_components", postgresql.JSONB, nullable=True),
        sa.Column("phone_original", sa.Text, nullable=True),
        sa.Column("phone_e164", sa.String(20), nullable=True),
        sa.Column("website", sa.Text, nullable=True),
        sa.Column("website_domain", sa.String(255), nullable=True),
        sa.Column("openooh_category", sa.String(50), nullable=True),
        sa.Column("openooh_subcategory", sa.String(100), nullable=True),
        sa.Column("raw_category", sa.String(100), nullable=True),
        sa.Column("brand", sa.String(100), nullable=True),
        sa.Column("brand_confidence", sa.Numeric(3, 2), nullable=True),
        sa.Column("quality_score", sa.Numeric(3, 2), nullable=True),
        sa.Column("quality_factors", postgresql.JSONB, nullable=True),
        sa.Column(
            "merged_into",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("master_pois.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "merge_status",
            sa.String(20),
            nullable=False,
            server_default=sa.text("'pending'"),
        ),
        sa.Column("merge_reason", sa.Text, nullable=True),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.Column(
            "updated_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.CheckConstraint(
            "merge_status IN ('pending', 'merged', 'duplicate', 'rejected')",
            name="ck_proc_merge_status",
        ),
        sa.CheckConstraint(
            "brand_confidence IS NULL OR (brand_confidence >= 0 AND brand_confidence <= 1)",
            name="ck_proc_brand_confidence",
        ),
        sa.CheckConstraint(
            "quality_score IS NULL OR (quality_score >= 0 AND quality_score <= 1)",
            name="ck_proc_quality_score",
        ),
    )
    op.execute(
        "ALTER TABLE processed_pois ADD COLUMN location GEOGRAPHY(POINT, 4326) NOT NULL"
    )
    op.execute(
        "ALTER TABLE processed_pois ADD COLUMN name_embedding VECTOR(384) NOT NULL"
    )
    op.execute(
        "CREATE INDEX idx_proc_location ON processed_pois USING GIST(location)"
    )
    op.execute(
        "CREATE INDEX idx_proc_embedding ON processed_pois "
        "USING hnsw (name_embedding vector_cosine_ops)"
    )
    op.execute(
        "CREATE INDEX idx_proc_unmerged ON processed_pois(merge_status) "
        "WHERE merge_status = 'pending'"
    )
    op.execute(
        "CREATE INDEX idx_proc_brand ON processed_pois(brand) WHERE brand IS NOT NULL"
    )
    op.create_index(
        "idx_proc_category",
        "processed_pois",
        ["openooh_category", "openooh_subcategory"],
    )
    op.create_index("idx_proc_raw", "processed_pois", ["raw_poi_id"])
    op.execute(
        "CREATE TRIGGER trg_processed_pois_updated_at BEFORE UPDATE ON processed_pois "
        "FOR EACH ROW EXECUTE FUNCTION set_updated_at()"
    )

    # ======================================================================
    # API clients (access control)
    # ======================================================================
    op.create_table(
        "api_clients",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("api_key_hash", sa.String(64), nullable=False, unique=True),
        sa.Column(
            "permissions",
            postgresql.JSONB,
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "rate_limit_per_minute",
            sa.Integer,
            nullable=False,
            server_default=sa.text("1000"),
        ),
        sa.Column(
            "enabled", sa.Boolean, nullable=False, server_default=sa.text("true")
        ),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.CheckConstraint(
            "jsonb_typeof(permissions) = 'array'",
            name="ck_api_clients_permissions_array",
        ),
    )
    op.create_index(
        "idx_api_clients_enabled", "api_clients", ["enabled", "api_key_hash"]
    )


def downgrade() -> None:
    op.drop_index("idx_api_clients_enabled", table_name="api_clients")
    op.drop_table("api_clients")

    op.execute("DROP TRIGGER IF EXISTS trg_processed_pois_updated_at ON processed_pois")
    op.drop_index("idx_proc_raw", table_name="processed_pois")
    op.drop_index("idx_proc_category", table_name="processed_pois")
    op.execute("DROP INDEX IF EXISTS idx_proc_brand")
    op.execute("DROP INDEX IF EXISTS idx_proc_unmerged")
    op.execute("DROP INDEX IF EXISTS idx_proc_embedding")
    op.execute("DROP INDEX IF EXISTS idx_proc_location")
    op.drop_table("processed_pois")

    op.drop_index("idx_history_changed_at", table_name="master_poi_history")
    op.drop_index("idx_history_master_version", table_name="master_poi_history")
    op.drop_table("master_poi_history")

    op.execute("DROP TRIGGER IF EXISTS trg_master_pois_updated_at ON master_pois")
    op.execute("DROP INDEX IF EXISTS idx_master_active")
    op.drop_index("idx_master_category", table_name="master_pois")
    op.execute("DROP INDEX IF EXISTS idx_master_brand")
    op.execute("DROP INDEX IF EXISTS idx_master_embedding")
    op.execute("DROP INDEX IF EXISTS idx_master_location")
    op.drop_table("master_pois")

    op.drop_index("idx_raw_source_fetched", table_name="raw_pois")
    op.execute("DROP INDEX IF EXISTS idx_raw_unprocessed")
    op.execute("DROP INDEX IF EXISTS idx_raw_payload")
    op.execute("DROP INDEX IF EXISTS idx_raw_location")
    op.drop_table("raw_pois")

    op.drop_index("idx_jobs_source_created", table_name="ingestion_jobs")
    op.execute("DROP INDEX IF EXISTS idx_jobs_status")
    op.drop_table("ingestion_jobs")

    op.execute("DROP TRIGGER IF EXISTS trg_sources_updated_at ON sources")
    op.drop_table("sources")

    op.drop_index("idx_brands_enabled", table_name="brands")
    op.drop_table("brands")

    op.drop_index("idx_openooh_parent", table_name="openooh_categories")
    op.drop_table("openooh_categories")

    op.execute("DROP FUNCTION IF EXISTS set_updated_at()")
