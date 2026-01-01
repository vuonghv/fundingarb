"""
Position management API routes.
"""

from typing import List, Optional
from fastapi import APIRouter, HTTPException, Depends, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from ...database.connection import get_session
from ...database.repository import PositionRepository
from ...database.models import PositionStatus
from ..schemas import (
    PositionResponse,
    PositionListResponse,
    OpenPositionRequest,
    ClosePositionRequest,
    TradeResponse,
    FundingEventResponse,
)

router = APIRouter()


def position_to_response(position) -> PositionResponse:
    """Convert Position model to response schema."""
    return PositionResponse(
        id=position.id,
        pair=position.pair,
        long_exchange=position.long_exchange,
        short_exchange=position.short_exchange,
        long_entry_price=float(position.long_entry_price) if position.long_entry_price else None,
        short_entry_price=float(position.short_entry_price) if position.short_entry_price else None,
        size_usd=float(position.size_usd),
        long_size=float(position.long_size) if position.long_size else None,
        short_size=float(position.short_size) if position.short_size else None,
        leverage_long=position.leverage_long,
        leverage_short=position.leverage_short,
        entry_timestamp=position.entry_timestamp,
        entry_funding_spread=float(position.entry_funding_spread) if position.entry_funding_spread else None,
        status=position.status.value,
        close_timestamp=position.close_timestamp,
        realized_pnl=float(position.realized_pnl) if position.realized_pnl else None,
        funding_collected=float(position.funding_collected),
        total_fees=float(position.total_fees),
    )


@router.get("", response_model=PositionListResponse)
async def get_positions(
    status: Optional[str] = Query(None, description="Filter by status: open, closed, all"),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
):
    """
    Get positions with optional filtering.

    - **status**: Filter by position status (open, closed, all)
    - **limit**: Maximum number of positions to return
    - **offset**: Offset for pagination
    """
    async with get_session() as session:
        repo = PositionRepository(session)

        if status == "open":
            positions = await repo.get_open_positions()
        elif status == "closed":
            positions = await repo.get_closed_positions(limit=limit, offset=offset)
        else:
            positions = await repo.get_all_positions(limit=limit)

        return PositionListResponse(
            positions=[position_to_response(p) for p in positions],
            total=len(positions),
        )


@router.get("/open", response_model=List[PositionResponse])
async def get_open_positions():
    """Get all open positions."""
    async with get_session() as session:
        repo = PositionRepository(session)
        positions = await repo.get_open_positions()
        return [position_to_response(p) for p in positions]


@router.get("/closed", response_model=List[PositionResponse])
async def get_closed_positions(
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
):
    """Get closed positions with pagination."""
    async with get_session() as session:
        repo = PositionRepository(session)
        positions = await repo.get_closed_positions(limit=limit, offset=offset)
        return [position_to_response(p) for p in positions]


@router.get("/{position_id}", response_model=PositionResponse)
async def get_position(position_id: str):
    """Get a specific position by ID."""
    async with get_session() as session:
        repo = PositionRepository(session)
        position = await repo.get_by_id(position_id)

        if not position:
            raise HTTPException(status_code=404, detail="Position not found")

        return position_to_response(position)


@router.post("/open", response_model=PositionResponse)
async def open_position(request: Request, body: OpenPositionRequest):
    """
    Manually open a new hedged position.

    This bypasses the automatic opportunity detection and immediately
    executes a position on the specified exchanges.
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
            detail="Engine must be running to open positions"
        )

    # Check risk limits
    can_open, reason = coordinator.risk_manager.can_open_position(
        body.pair,
        body.size_usd,
    )
    if not can_open:
        raise HTTPException(
            status_code=400,
            detail=f"Cannot open position: {reason}"
        )

    try:
        # Create a manual opportunity object
        from ...engine.detector import ArbitrageOpportunity
        from decimal import Decimal

        # Get current rates for the pair
        rates = coordinator.scanner.get_rates_for_symbol(body.pair)
        if body.long_exchange not in rates or body.short_exchange not in rates:
            raise HTTPException(
                status_code=400,
                detail=f"Funding rates not available for {body.pair} on specified exchanges"
            )

        long_rate = rates[body.long_exchange]
        short_rate = rates[body.short_exchange]

        opportunity = ArbitrageOpportunity(
            symbol=body.pair,
            long_exchange=body.long_exchange,
            short_exchange=body.short_exchange,
            long_rate=long_rate.rate,
            short_rate=short_rate.rate,
            spread=short_rate.rate - long_rate.rate,
            daily_spread=(short_rate.daily_rate - long_rate.daily_rate),
            long_interval_hours=long_rate.interval_hours,
            short_interval_hours=short_rate.interval_hours,
            expected_daily_profit_usd=float(short_rate.daily_rate - long_rate.daily_rate) * body.size_usd,
            seconds_to_funding=min(
                (long_rate.next_funding_time - long_rate.timestamp).total_seconds(),
                (short_rate.next_funding_time - short_rate.timestamp).total_seconds(),
            ),
        )

        # Execute via coordinator
        async with get_session() as session:
            from ...engine.position_manager import PositionManager
            position_manager = PositionManager(session, coordinator.exchanges)
            await coordinator._execute_opportunity(position_manager, opportunity)

            # Get the created position
            positions = await position_manager.get_open_positions()
            for pos in positions:
                if pos.pair == body.pair:
                    return position_to_response(pos)

        raise HTTPException(
            status_code=500,
            detail="Position created but could not be retrieved"
        )

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to open position: {str(e)}"
        )


@router.post("/{position_id}/close")
async def close_position(
    request: Request,
    position_id: str,
    body: ClosePositionRequest = None,
):
    """
    Close a specific position.

    This will market close both legs of the hedged position.
    """
    coordinator = getattr(request.app.state, 'coordinator', None)

    if not coordinator:
        raise HTTPException(
            status_code=503,
            detail="Trading coordinator not initialized"
        )

    reason = body.reason if body else "manual"

    try:
        success = await coordinator.close_position(position_id, reason)

        if success:
            # Get the closed position for response
            async with get_session() as session:
                repo = PositionRepository(session)
                position = await repo.get_by_id(position_id)
                if position:
                    return position_to_response(position)

            return {"success": True, "message": f"Position {position_id} closed"}
        else:
            raise HTTPException(
                status_code=500,
                detail="Failed to close position"
            )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to close position: {str(e)}"
        )


@router.get("/{position_id}/trades", response_model=List[TradeResponse])
async def get_position_trades(position_id: str):
    """Get all trades for a position."""
    async with get_session() as session:
        from ...database.repository import TradeRepository
        repo = TradeRepository(session)
        trades = await repo.get_trades_for_position(position_id)

        return [
            TradeResponse(
                id=t.id,
                position_id=t.position_id,
                exchange=t.exchange,
                pair=t.pair,
                side=t.side.value,
                action=t.action.value,
                order_type=t.order_type.value,
                price=float(t.price) if t.price else None,
                size=float(t.size),
                fee=float(t.fee),
                order_id=t.order_id,
                status=t.status.value,
                executed_at=t.executed_at,
            )
            for t in trades
        ]


@router.get("/{position_id}/funding", response_model=List[FundingEventResponse])
async def get_position_funding(position_id: str):
    """Get all funding events for a position."""
    async with get_session() as session:
        from ...database.repository import FundingEventRepository
        repo = FundingEventRepository(session)
        events = await repo.get_events_for_position(position_id)

        return [
            FundingEventResponse(
                id=e.id,
                position_id=e.position_id,
                exchange=e.exchange,
                pair=e.pair,
                side=e.side.value,
                funding_rate=float(e.funding_rate),
                payment_usd=float(e.payment_usd),
                position_size=float(e.position_size),
                timestamp=e.timestamp,
            )
            for e in events
        ]
