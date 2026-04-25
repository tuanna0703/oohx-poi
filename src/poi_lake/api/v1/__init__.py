"""v1 API routers."""

from fastapi import APIRouter

from poi_lake.api.v1.admin import router as admin_router

api_v1_router = APIRouter(prefix="/api/v1")
api_v1_router.include_router(admin_router)

__all__ = ["api_v1_router"]
