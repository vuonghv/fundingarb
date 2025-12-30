"""
Trading engine control API routes.
"""

from fastapi import APIRouter, HTTPException

from ..schemas import (
    EngineStatusResponse,
    EngineActionResponse,
    KillSwitchRequest,
    RiskStatusResponse,
    StatsResponse,
    FundingRatesResponse,
    FundingRateResponse,
    OpportunitiesResponse,
    OpportunityResponse,
)

router = APIRouter()

# These routes need the trading coordinator to be injected
# In production, use FastAPI dependencies


@router.get("/status", response_model=EngineStatusResponse)
async def get_engine_status():
    """
    Get current trading engine status.

    Returns the engine state, connected exchanges, and other metrics.
    """
    # This needs to be wired to the actual coordinator
    # For now, return a placeholder
    return EngineStatusResponse(
        state="STOPPED",
        simulation_mode=True,
        connected_exchanges=[],
        monitored_symbols=[],
        open_positions=0,
        last_scan_time=None,
        last_opportunity_time=None,
        pending_orders=0,
        kill_switch_active=False,
        error_message=None,
    )


@router.post("/start", response_model=EngineActionResponse)
async def start_engine():
    """
    Start the trading engine.

    Begins monitoring for arbitrage opportunities and executing trades.
    """
    # This needs coordinator integration
    raise HTTPException(
        status_code=501,
        detail="Engine control requires trading coordinator integration"
    )


@router.post("/stop", response_model=EngineActionResponse)
async def stop_engine():
    """
    Stop the trading engine.

    Stops monitoring but keeps existing positions open.
    """
    raise HTTPException(
        status_code=501,
        detail="Engine control requires trading coordinator integration"
    )


@router.post("/kill", response_model=EngineActionResponse)
async def activate_kill_switch(request: KillSwitchRequest):
    """
    Activate the kill switch.

    This will:
    1. Cancel all pending orders
    2. Close all open positions at market
    3. Halt all trading automation
    4. Require manual restart

    **This action cannot be undone automatically.**
    """
    if not request.confirm:
        raise HTTPException(
            status_code=400,
            detail="Must confirm kill switch activation"
        )

    raise HTTPException(
        status_code=501,
        detail="Kill switch requires trading coordinator integration"
    )


@router.post("/kill/deactivate", response_model=EngineActionResponse)
async def deactivate_kill_switch():
    """
    Deactivate the kill switch.

    This re-enables trading but does not automatically restart the engine.
    """
    raise HTTPException(
        status_code=501,
        detail="Kill switch requires trading coordinator integration"
    )


@router.get("/risk", response_model=RiskStatusResponse)
async def get_risk_status():
    """
    Get current risk management status.

    Returns kill switch state, paused pairs, and position limits.
    """
    return RiskStatusResponse(
        kill_switch_active=False,
        kill_switch_activated_at=None,
        trading_enabled=True,
        paused_pairs={},
        max_position_per_pair=50000.0,
    )


@router.get("/stats", response_model=StatsResponse)
async def get_trading_stats():
    """
    Get trading statistics.

    Returns aggregate performance metrics.
    """
    from ...database.connection import get_session
    from ...database.repository import PositionRepository

    async with get_session() as session:
        repo = PositionRepository(session)

        open_count = await repo.count_open_positions()
        total_pnl = await repo.get_total_pnl()
        total_funding = await repo.get_total_funding()

        closed = await repo.get_closed_positions(limit=1000)
        total_positions = len(closed) + open_count

        # Calculate win rate
        profitable = sum(1 for p in closed if p.realized_pnl and p.realized_pnl > 0)
        win_rate = (profitable / len(closed) * 100) if closed else 0

        return StatsResponse(
            total_positions=total_positions,
            open_positions=open_count,
            closed_positions=len(closed),
            total_realized_pnl=float(total_pnl),
            total_funding_collected=float(total_funding),
            total_fees_paid=0,  # Would need to calculate
            win_rate=win_rate,
            average_hold_time_hours=None,
        )


@router.get("/rates", response_model=dict)
async def get_funding_rates():
    """
    Get current funding rates from all exchanges.

    Returns the latest funding rates being monitored.
    """
    # This needs the scanner to be wired
    return {"rates": {}, "message": "Requires scanner integration"}


@router.get("/opportunities", response_model=dict)
async def get_opportunities():
    """
    Get current arbitrage opportunities.

    Returns opportunities above the spread threshold.
    """
    # This needs the detector to be wired
    return {"opportunities": [], "message": "Requires detector integration"}


@router.post("/scan", response_model=EngineActionResponse)
async def force_scan():
    """
    Force a funding rate scan.

    Triggers an immediate scan for opportunities outside the normal schedule.
    """
    raise HTTPException(
        status_code=501,
        detail="Force scan requires scanner integration"
    )
