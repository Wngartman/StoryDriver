from fastapi import APIRouter

from app.config import APP_NAME
from app.schemas import HealthResponse


router = APIRouter(tags=["health"])


@router.get("/health", response_model=HealthResponse)
def get_health() -> HealthResponse:
    return HealthResponse(ok=True, app=APP_NAME)
