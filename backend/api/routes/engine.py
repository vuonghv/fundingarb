"""
Trading engine control API routes.
"""

from fastapi import APIRouter, HTTPException, Request

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
async def get_engine_status(request: Request):
    """
    Get current trading engine status.

    Returns the engine state, connected exchanges, and other metrics.
    """
    coordinator = getattr(request.app.state, 'coordinator', None)

    if coordinator:
        status = coordinator.get_status()
        return EngineStatusResponse(
            state=status.state.value,
            simulation_mode=status.simulation_mode,
            connected_exchanges=status.connected_exchanges,
            monitored_symbols=status.monitored_symbols,
            open_positions=status.open_positions,
            last_scan_time=status.last_scan_time,
            last_opportunity_time=status.last_opportunity_time,
            pending_orders=status.pending_orders,
            kill_switch_active=status.kill_switch_active,
            error_message=status.error_message,
        )

    # Fallback if coordinator not available
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
async def start_engine(request: Request):
    """
    Start the trading engine.

    Begins monitoring for arbitrage opportunities and executing trades.
    """
    coordinator = getattr(request.app.state, 'coordinator', None)

    if not coordinator:
        raise HTTPException(
            status_code=503,
            detail="Trading coordinator not initialized"
        )

    try:
        await coordinator.start()
        return EngineActionResponse(
            success=True,
            action="start",
            message="Engine started successfully",
            new_state=coordinator.state.value,
        )
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to start engine: {str(e)}"
        )


@router.post("/stop", response_model=EngineActionResponse)
async def stop_engine(request: Request):
    """
    Stop the trading engine.

    Stops monitoring but keeps existing positions open.
    """
    coordinator = getattr(request.app.state, 'coordinator', None)

    if not coordinator:
        raise HTTPException(
            status_code=503,
            detail="Trading coordinator not initialized"
        )

    try:
        await coordinator.stop()
        return EngineActionResponse(
            success=True,
            action="stop",
            message="Engine stopped successfully",
            new_state=coordinator.state.value,
        )
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to stop engine: {str(e)}"
        )


@router.post("/kill", response_model=EngineActionResponse)
async def activate_kill_switch(request: Request, body: KillSwitchRequest):
    """
    Activate the kill switch.

    This will:
    1. Cancel all pending orders
    2. Close all open positions at market
    3. Halt all trading automation
    4. Require manual restart

    **This action cannot be undone automatically.**
    """
    if not body.confirm:
        raise HTTPException(
            status_code=400,
            detail="Must confirm kill switch activation"
        )

    coordinator = getattr(request.app.state, 'coordinator', None)

    if not coordinator:
        raise HTTPException(
            status_code=503,
            detail="Trading coordinator not initialized"
        )

    try:
        reason = body.reason or "Manual activation via API"
        await coordinator.activate_kill_switch(reason)
        return EngineActionResponse(
            success=True,
            action="kill",
            message=f"Kill switch activated: {reason}",
            new_state=coordinator.state.value,
        )
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to activate kill switch: {str(e)}"
        )


@router.post("/kill/deactivate", response_model=EngineActionResponse)
async def deactivate_kill_switch(request: Request):
    """
    Deactivate the kill switch.

    This re-enables trading but does not automatically restart the engine.
    """
    coordinator = getattr(request.app.state, 'coordinator', None)

    if not coordinator:
        raise HTTPException(
            status_code=503,
            detail="Trading coordinator not initialized"
        )

    try:
        coordinator.deactivate_kill_switch()
        return EngineActionResponse(
            success=True,
            action="deactivate_kill",
            message="Kill switch deactivated",
            new_state=coordinator.state.value,
        )
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to deactivate kill switch: {str(e)}"
        )


@router.get("/risk", response_model=RiskStatusResponse)
async def get_risk_status(request: Request):
    """
    Get current risk management status.

    Returns kill switch state, paused pairs, and position limits.
    """
    coordinator = getattr(request.app.state, 'coordinator', None)
    config = getattr(request.app.state, 'config', None)

    if coordinator and coordinator.risk_manager:
        rm = coordinator.risk_manager
        return RiskStatusResponse(
            kill_switch_active=rm.is_kill_switch_active,
            kill_switch_activated_at=rm.kill_switch_activated_at,
            trading_enabled=rm.is_trading_enabled,
            paused_pairs=rm.paused_pairs,
            max_position_per_pair=float(config.trading.max_position_per_pair_usd) if config else 50000.0,
        )

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
async def get_funding_rates(request: Request):
    """
    Get current funding rates from all exchanges.

    Returns the latest funding rates being monitored.
    """
    from datetime import datetime, timezone

    config = getattr(request.app.state, 'config', None)
    exchanges = getattr(request.app.state, 'exchanges', {})

    if not exchanges:
        return {"rates": [], "message": "No exchanges connected"}

    # Get symbols from config
    symbols = []
    if config and config.trading:
        symbols = config.trading.symbols

    if not symbols:
        symbols = ["BTC/USDT:USDT", "ETH/USDT:USDT", "SOL/USDT:USDT"]

    rates = []

    # Fetch rates from each exchange
    for exchange_name, exchange in exchanges.items():
        try:
            exchange_rates = await exchange.get_funding_rates(symbols)
            for symbol, rate in exchange_rates.items():
                # Calculate time to next funding in seconds
                next_funding_seconds = 0
                if rate.next_funding_time:
                    delta = rate.next_funding_time - datetime.now(timezone.utc)
                    next_funding_seconds = max(0, int(delta.total_seconds()))

                # Format pair for display (e.g., "BTC/USDT:USDT" -> "BTC/USDT")
                display_pair = symbol.split(":")[0] if ":" in symbol else symbol

                # Rate is already a decimal like 0.0001 (= 0.01%)
                # Frontend expects percentage value for display (0.01 shows as "0.0100%")
                rate_pct = float(rate.rate_percent)

                # Use predicted_rate if available and valid, otherwise use current rate
                # Note: predicted_rate might be None or incorrectly set to mark price
                predicted_pct = rate_pct
                if rate.predicted_rate is not None and rate.predicted_rate != 0:
                    # Only use if it looks like a valid funding rate (small value)
                    pred_val = float(rate.predicted_rate)
                    if abs(pred_val) < 1:  # Valid funding rates are < 1 (< 100%)
                        predicted_pct = pred_val * 100

                rates.append({
                    "exchange": exchange_name.capitalize(),
                    "pair": display_pair,
                    "rate": rate_pct,  # Per-interval rate as percentage
                    "dailyRate": float(rate.daily_rate_percent),  # Daily normalized rate as percentage
                    "predicted": predicted_pct,
                    "nextFunding": next_funding_seconds,
                    "intervalHours": rate.interval_hours,
                    "markPrice": str(rate.mark_price) if rate.mark_price else None,
                    "indexPrice": str(rate.index_price) if rate.index_price else None,
                })
        except Exception as e:
            # Log but continue with other exchanges
            import logging
            logging.warning(f"Failed to get rates from {exchange_name}: {e}")

    return {"rates": rates}


@router.get("/opportunities", response_model=dict)
async def get_opportunities(request: Request):
    """
    Get current arbitrage opportunities.

    Returns opportunities above the spread threshold.
    """
    coordinator = getattr(request.app.state, 'coordinator', None)

    if coordinator and coordinator.detector:
        opportunities = coordinator.detector.last_opportunities
        return {
            "opportunities": [
                {
                    "symbol": opp.symbol,
                    "long_exchange": opp.long_exchange,
                    "short_exchange": opp.short_exchange,
                    "spread": float(opp.spread),
                    "daily_spread": float(opp.daily_spread),
                    "expected_daily_profit_usd": float(opp.expected_daily_profit_usd),
                    "long_rate": float(opp.long_rate),
                    "short_rate": float(opp.short_rate),
                    "seconds_to_funding": opp.seconds_to_funding,
                }
                for opp in opportunities
            ],
            "count": len(opportunities),
        }

    return {"opportunities": [], "count": 0}


@router.post("/scan", response_model=EngineActionResponse)
async def force_scan(request: Request):
    """
    Force a funding rate scan.

    Triggers an immediate scan for opportunities outside the normal schedule.
    """
    coordinator = getattr(request.app.state, 'coordinator', None)

    if not coordinator:
        raise HTTPException(
            status_code=503,
            detail="Trading coordinator not initialized"
        )

    if not coordinator.is_running:
        raise HTTPException(
            status_code=400,
            detail="Engine must be running to force scan"
        )

    try:
        # Trigger a manual rate fetch which will update opportunities
        await coordinator.scanner._fetch_all_rates()
        return EngineActionResponse(
            success=True,
            action="scan",
            message="Scan triggered successfully",
            new_state=coordinator.state.value,
        )
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to trigger scan: {str(e)}"
        )
