"""
Repository pattern for data access.

Provides a clean interface for database operations,
abstracting away SQLAlchemy details from the business logic.
"""

from datetime import datetime, timezone
from decimal import Decimal
from typing import List, Optional
from uuid import UUID

from sqlalchemy import select, update, delete, func, and_
from sqlalchemy.ext.asyncio import AsyncSession

from .models import (
    Position,
    Trade,
    FundingEvent,
    SystemState,
    PositionStatus,
    OrderSide,
    OrderAction,
    TradeStatus,
)


class PositionRepository:
    """Repository for Position operations."""

    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_by_id(self, position_id: str) -> Optional[Position]:
        """Get a position by ID."""
        result = await self.session.execute(
            select(Position).where(Position.id == position_id)
        )
        return result.scalar_one_or_none()

    async def get_open_positions(self) -> List[Position]:
        """Get all open positions."""
        result = await self.session.execute(
            select(Position)
            .where(Position.status == PositionStatus.OPEN)
            .order_by(Position.entry_timestamp.desc())
        )
        return list(result.scalars().all())

    async def get_open_position_for_pair(self, pair: str) -> Optional[Position]:
        """Get open position for a specific pair (max 1 per pair)."""
        result = await self.session.execute(
            select(Position)
            .where(Position.pair == pair)
            .where(Position.status == PositionStatus.OPEN)
        )
        return result.scalar_one_or_none()

    async def get_closed_positions(
        self,
        limit: int = 100,
        offset: int = 0,
    ) -> List[Position]:
        """Get closed positions with pagination."""
        result = await self.session.execute(
            select(Position)
            .where(Position.status.in_([PositionStatus.CLOSED, PositionStatus.LIQUIDATED]))
            .order_by(Position.close_timestamp.desc())
            .limit(limit)
            .offset(offset)
        )
        return list(result.scalars().all())

    async def get_all_positions(self, limit: int = 100) -> List[Position]:
        """Get all positions."""
        result = await self.session.execute(
            select(Position)
            .order_by(Position.entry_timestamp.desc())
            .limit(limit)
        )
        return list(result.scalars().all())

    async def create(self, position: Position) -> Position:
        """Create a new position."""
        self.session.add(position)
        await self.session.flush()
        await self.session.refresh(position)
        return position

    async def update(self, position_id: str, **kwargs) -> None:
        """Update position fields."""
        await self.session.execute(
            update(Position)
            .where(Position.id == position_id)
            .values(**kwargs)
        )
        await self.session.flush()

    async def close_position(
        self,
        position_id: str,
        status: PositionStatus,
        realized_pnl: Decimal,
        long_close_price: Decimal,
        short_close_price: Decimal,
    ) -> None:
        """Close a position with final P&L calculation."""
        await self.session.execute(
            update(Position)
            .where(Position.id == position_id)
            .values(
                status=status,
                close_timestamp=datetime.now(timezone.utc),
                realized_pnl=realized_pnl,
                long_close_price=long_close_price,
                short_close_price=short_close_price,
            )
        )
        await self.session.flush()

    async def add_funding(self, position_id: str, amount: Decimal) -> None:
        """Add funding payment to position."""
        position = await self.get_by_id(position_id)
        if position:
            new_funding = position.funding_collected + amount
            await self.update(position_id, funding_collected=new_funding)

    async def add_fees(self, position_id: str, fee: Decimal) -> None:
        """Add fees to position."""
        position = await self.get_by_id(position_id)
        if position:
            new_fees = position.total_fees + fee
            await self.update(position_id, total_fees=new_fees)

    async def count_open_positions(self) -> int:
        """Count open positions."""
        result = await self.session.execute(
            select(func.count(Position.id))
            .where(Position.status == PositionStatus.OPEN)
        )
        return result.scalar() or 0

    async def get_total_pnl(self) -> Decimal:
        """Get total realized P&L across all closed positions."""
        result = await self.session.execute(
            select(func.sum(Position.realized_pnl))
            .where(Position.status.in_([PositionStatus.CLOSED, PositionStatus.LIQUIDATED]))
        )
        return result.scalar() or Decimal("0")

    async def get_total_funding(self) -> Decimal:
        """Get total funding collected across all positions."""
        result = await self.session.execute(
            select(func.sum(Position.funding_collected))
        )
        return result.scalar() or Decimal("0")


class TradeRepository:
    """Repository for Trade operations."""

    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_by_id(self, trade_id: str) -> Optional[Trade]:
        """Get a trade by ID."""
        result = await self.session.execute(
            select(Trade).where(Trade.id == trade_id)
        )
        return result.scalar_one_or_none()

    async def get_by_order_id(self, exchange: str, order_id: str) -> Optional[Trade]:
        """Get a trade by exchange order ID."""
        result = await self.session.execute(
            select(Trade)
            .where(Trade.exchange == exchange)
            .where(Trade.order_id == order_id)
        )
        return result.scalar_one_or_none()

    async def get_trades_for_position(self, position_id: str) -> List[Trade]:
        """Get all trades for a position."""
        result = await self.session.execute(
            select(Trade)
            .where(Trade.position_id == position_id)
            .order_by(Trade.created_at)
        )
        return list(result.scalars().all())

    async def get_recent_trades(self, limit: int = 50) -> List[Trade]:
        """Get recent trades."""
        result = await self.session.execute(
            select(Trade)
            .order_by(Trade.created_at.desc())
            .limit(limit)
        )
        return list(result.scalars().all())

    async def create(self, trade: Trade) -> Trade:
        """Create a new trade."""
        self.session.add(trade)
        await self.session.flush()
        await self.session.refresh(trade)
        return trade

    async def update(self, trade_id: str, **kwargs) -> None:
        """Update trade fields."""
        await self.session.execute(
            update(Trade)
            .where(Trade.id == trade_id)
            .values(**kwargs)
        )
        await self.session.flush()

    async def mark_filled(
        self,
        trade_id: str,
        price: Decimal,
        fee: Decimal,
        order_id: str,
        latency_ms: int,
    ) -> None:
        """Mark a trade as filled."""
        await self.session.execute(
            update(Trade)
            .where(Trade.id == trade_id)
            .values(
                status=TradeStatus.FILLED,
                price=price,
                fee=fee,
                order_id=order_id,
                latency_ms=latency_ms,
                executed_at=datetime.now(timezone.utc),
            )
        )
        await self.session.flush()

    async def mark_failed(self, trade_id: str, error_message: str) -> None:
        """Mark a trade as failed."""
        await self.session.execute(
            update(Trade)
            .where(Trade.id == trade_id)
            .values(
                status=TradeStatus.FAILED,
                error_message=error_message,
            )
        )
        await self.session.flush()

    async def get_pending_trades(self) -> List[Trade]:
        """Get all pending trades."""
        result = await self.session.execute(
            select(Trade)
            .where(Trade.status == TradeStatus.PENDING)
            .order_by(Trade.created_at)
        )
        return list(result.scalars().all())


class FundingEventRepository:
    """Repository for FundingEvent operations."""

    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_by_id(self, event_id: str) -> Optional[FundingEvent]:
        """Get a funding event by ID."""
        result = await self.session.execute(
            select(FundingEvent).where(FundingEvent.id == event_id)
        )
        return result.scalar_one_or_none()

    async def get_events_for_position(self, position_id: str) -> List[FundingEvent]:
        """Get all funding events for a position."""
        result = await self.session.execute(
            select(FundingEvent)
            .where(FundingEvent.position_id == position_id)
            .order_by(FundingEvent.timestamp)
        )
        return list(result.scalars().all())

    async def get_recent_events(self, limit: int = 50) -> List[FundingEvent]:
        """Get recent funding events."""
        result = await self.session.execute(
            select(FundingEvent)
            .order_by(FundingEvent.timestamp.desc())
            .limit(limit)
        )
        return list(result.scalars().all())

    async def create(self, event: FundingEvent) -> FundingEvent:
        """Create a new funding event."""
        self.session.add(event)
        await self.session.flush()
        await self.session.refresh(event)
        return event

    async def get_total_funding_for_position(self, position_id: str) -> Decimal:
        """Get total funding for a position."""
        result = await self.session.execute(
            select(func.sum(FundingEvent.payment_usd))
            .where(FundingEvent.position_id == position_id)
        )
        return result.scalar() or Decimal("0")


class SystemStateRepository:
    """Repository for SystemState operations (key-value storage)."""

    def __init__(self, session: AsyncSession):
        self.session = session

    async def get(self, key: str) -> Optional[str]:
        """Get a state value by key."""
        result = await self.session.execute(
            select(SystemState.value).where(SystemState.key == key)
        )
        return result.scalar_one_or_none()

    async def set(self, key: str, value: str) -> None:
        """Set a state value."""
        # Try to update first
        result = await self.session.execute(
            update(SystemState)
            .where(SystemState.key == key)
            .values(value=value, updated_at=datetime.now(timezone.utc))
        )

        # If no rows updated, insert
        if result.rowcount == 0:
            state = SystemState(key=key, value=value)
            self.session.add(state)

        await self.session.flush()

    async def delete(self, key: str) -> None:
        """Delete a state value."""
        await self.session.execute(
            delete(SystemState).where(SystemState.key == key)
        )
        await self.session.flush()

    async def get_all(self) -> dict:
        """Get all state values as a dictionary."""
        result = await self.session.execute(
            select(SystemState)
        )
        return {state.key: state.value for state in result.scalars().all()}
