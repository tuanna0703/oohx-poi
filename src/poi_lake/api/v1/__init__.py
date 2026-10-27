"""v1 API routers."""

from fastapi import APIRouter

from poi_lake.api.v1.admin import router as admin_router
from poi_lake.api.v1.brand_discovery import router as brand_discovery_router
from poi_lake.api.v1.brands import router as brands_router
from poi_lake.api.v1.catalog import admin_router as catalog_admin_router
from poi_lake.api.v1.catalog import router as catalog_router
from poi_lake.api.v1.crawl_plan import router as crawl_plan_router
from poi_lake.api.v1.master_pois import router as master_pois_router
from poi_lake.api.v1.reports import admin_router as reports_admin_router
from poi_lake.api.v1.reports import router as reports_router

api_v1_router = APIRouter(prefix="/api/v1")
api_v1_router.include_router(admin_router)
api_v1_router.include_router(brand_discovery_router)
api_v1_router.include_router(brands_router)
api_v1_router.include_router(catalog_admin_router)
api_v1_router.include_router(catalog_router)
api_v1_router.include_router(crawl_plan_router)
api_v1_router.include_router(master_pois_router)
api_v1_router.include_router(reports_admin_router)
api_v1_router.include_router(reports_router)

__all__ = ["api_v1_router"]
