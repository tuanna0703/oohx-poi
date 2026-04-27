"""SQLAlchemy ORM models — one module per table."""

from poi_lake.db.models.admin_unit import AdminUnit
from poi_lake.db.models.api_client import APIClient
from poi_lake.db.models.brand import Brand
from poi_lake.db.models.crawl_plan import CrawlPlan, CrawlPlanStatus
from poi_lake.db.models.ingestion_job import IngestionJob, IngestionJobStatus, IngestionJobType
from poi_lake.db.models.master_poi import MasterPOI, MasterPOIStatus
from poi_lake.db.models.master_poi_history import MasterPOIHistory
from poi_lake.db.models.openooh_category import OpenOOHCategory
from poi_lake.db.models.processed_poi import MergeStatus, ProcessedPOI
from poi_lake.db.models.raw_poi import RawPOI
from poi_lake.db.models.source import Source

__all__ = [
    "AdminUnit",
    "APIClient",
    "Brand",
    "CrawlPlan",
    "CrawlPlanStatus",
    "IngestionJob",
    "IngestionJobStatus",
    "IngestionJobType",
    "MasterPOI",
    "MasterPOIHistory",
    "MasterPOIStatus",
    "MergeStatus",
    "OpenOOHCategory",
    "ProcessedPOI",
    "RawPOI",
    "Source",
]
