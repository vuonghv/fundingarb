"""
Position management API routes.
"""

from typing import List, Optional
from fastapi import APIRouter, HTTPException, Depends, Query
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
async def open_position(request: OpenPositionRequest):
    """
    Manually open a new hedged position.

    This bypasses the automatic opportunity detection and immediately
    executes a position on the specified exchanges.
    """
    # This would need the trading coordinator - for now return error
    raise HTTPException(
        status_code=501,
        detail="Manual position opening requires trading engine integration"
    )


@router.post("/{position_id}/close")
async def close_position(
    position_id: str,
    request: ClosePositionRequest = None,
):
    """
    Close a specific position.

    This will market close both legs of the hedged position.
    """
    # This would need the trading coordinator
    raise HTTPException(
        status_code=501,
        detail="Position closing requires trading engine integration"
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
