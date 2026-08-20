from fastapi import APIRouter

from app.services.resource_status import build_resource_status


router = APIRouter(tags=["resources"])


@router.get("/resource-status")
async def resource_status() -> dict:
    return await build_resource_status()
