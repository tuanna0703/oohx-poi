"""v1 API routers."""

from fastapi import APIRouter

from poi_lake.api.v1.admin import router as admin_router
from poi_lake.api.v1.crawl_plan import router as crawl_plan_router
from poi_lake.api.v1.master_pois import router as master_pois_router

api_v1_router = APIRouter(prefix="/api/v1")
api_v1_router.include_router(admin_router)
api_v1_router.include_router(crawl_plan_router)
api_v1_router.include_router(master_pois_router)

__all__ = ["api_v1_router"]
