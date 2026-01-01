"""
Health check API routes.
"""

from datetime import datetime, timezone
from fastapi import APIRouter, Request
from sqlalchemy import text

from ..schemas import HealthCheckResponse

router = APIRouter()


@router.get("/health", response_model=HealthCheckResponse)
async def health_check(request: Request):
    """
    Health check endpoint.

    Returns the health status of all system components.
    """
    # Check database
    db_healthy = True
    try:
        from ...database.connection import get_session
        async with get_session() as session:
            await session.execute(text("SELECT 1"))
    except Exception:
        db_healthy = False

    # Get exchange status from app state
    exchanges = getattr(request.app.state, 'exchanges', {})
    exchanges_status = {}
    for name, exchange in exchanges.items():
        try:
            exchanges_status[name] = {
                "connected": exchange.is_connected,
                "circuit_breaker_open": getattr(exchange, '_circuit_breaker_open', False),
            }
        except Exception:
            exchanges_status[name] = {"connected": False, "error": "Failed to get status"}

    # Get engine status from coordinator
    coordinator = getattr(request.app.state, 'coordinator', None)
    engine_running = False
    if coordinator:
        engine_running = coordinator.is_running

    # Determine overall status
    all_exchanges_connected = all(
        s.get("connected", False) for s in exchanges_status.values()
    ) if exchanges_status else False

    if db_healthy and engine_running and all_exchanges_connected:
        status = "healthy"
    elif db_healthy and (engine_running or all_exchanges_connected):
        status = "degraded"
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
