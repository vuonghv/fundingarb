"""
Health check API routes.
"""

from datetime import datetime, timezone
from fastapi import APIRouter

from ..schemas import HealthCheckResponse

router = APIRouter()


@router.get("/health", response_model=HealthCheckResponse)
async def health_check():
    """
    Health check endpoint.

    Returns the health status of all system components.
    """
    # Check database
    db_healthy = True
    try:
        from ...database.connection import get_session
        async with get_session() as session:
            await session.execute("SELECT 1")
    except Exception:
        db_healthy = False

    # Exchange status would need the exchanges dict
    exchanges_status = {}

    # Engine status would need the coordinator
    engine_running = False

    # Determine overall status
    if db_healthy and engine_running:
        status = "healthy"
    elif db_healthy:
        status = "degraded"
    else:
        status = "unhealthy"

    return HealthCheckResponse(
        status=status,
        database=db_healthy,
        exchanges=exchanges_status,
        engine_running=engine_running,
        timestamp=datetime.now(timezone.utc),
    )


@router.get("/ready")
async def readiness_check():
    """
    Readiness check endpoint.

    Returns 200 if the service is ready to accept traffic.
    """
    return {"ready": True}


@router.get("/live")
async def liveness_check():
    """
    Liveness check endpoint.

    Returns 200 if the service is alive.
    """
    return {"alive": True}
