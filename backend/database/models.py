"""
SQLAlchemy ORM models for the trading system.

Models match the database schema defined in SPEC.md.
"""

import enum
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import List, Optional

from sqlalchemy import (
    Column,
    String,
    Integer,
    Numeric,
    DateTime,
    ForeignKey,
    Enum,
    Boolean,
    Text,
    Index,
)
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    """Base class for all models."""
    pass


class PositionStatus(enum.Enum):
    """Position lifecycle status."""
    OPEN = "OPEN"
    CLOSED = "CLOSED"
    LIQUIDATED = "LIQUIDATED"


class OrderSide(enum.Enum):
    """Order side."""
    LONG = "LONG"
    SHORT = "SHORT"


class OrderAction(enum.Enum):
    """Order action type."""
    OPEN = "OPEN"
    CLOSE = "CLOSE"


class OrderType(enum.Enum):
    """Order type."""
    LIMIT = "LIMIT"
    MARKET = "MARKET"


class TradeStatus(enum.Enum):
    """Trade execution status."""
    PENDING = "PENDING"
    FILLED = "FILLED"
    CANCELLED = "CANCELLED"
    FAILED = "FAILED"


def generate_uuid() -> str:
    """Generate a UUID string."""
    return str(uuid.uuid4())


def utc_now() -> datetime:
    """Get current UTC datetime."""
    return datetime.now(timezone.utc)


class Position(Base):
    """
    Represents a hedged arbitrage position across two exchanges.

    A position consists of:
    - LONG leg on one exchange (lower funding rate)
    - SHORT leg on another exchange (higher funding rate)
    """
    __tablename__ = "positions"

    # Primary key
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=generate_uuid)

    # Position identifiers
    pair: Mapped[str] = mapped_column(String(20), nullable=False, index=True)

    # Exchange legs
    long_exchange: Mapped[str] = mapped_column(String(20), nullable=False)
    short_exchange: Mapped[str] = mapped_column(String(20), nullable=False)

    # Entry prices
    long_entry_price: Mapped[Optional[Decimal]] = mapped_column(Numeric(20, 8))
    short_entry_price: Mapped[Optional[Decimal]] = mapped_column(Numeric(20, 8))

    # Position size
    size_usd: Mapped[Decimal] = mapped_column(Numeric(20, 2), nullable=False)
    long_size: Mapped[Optional[Decimal]] = mapped_column(Numeric(20, 8))  # Size in contracts
    short_size: Mapped[Optional[Decimal]] = mapped_column(Numeric(20, 8))

    # Leverage
    leverage_long: Mapped[int] = mapped_column(Integer, default=1)
    leverage_short: Mapped[int] = mapped_column(Integer, default=1)

    # Timestamps
    entry_timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now
    )
    close_timestamp: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))

    # Entry spread
    entry_funding_spread: Mapped[Optional[Decimal]] = mapped_column(Numeric(10, 6))

    # Status
    status: Mapped[PositionStatus] = mapped_column(
        Enum(PositionStatus), default=PositionStatus.OPEN, index=True
    )

    # P&L tracking
    realized_pnl: Mapped[Optional[Decimal]] = mapped_column(Numeric(20, 2))
    funding_collected: Mapped[Decimal] = mapped_column(Numeric(20, 2), default=Decimal("0"))

    # Total fees paid
    total_fees: Mapped[Decimal] = mapped_column(Numeric(20, 8), default=Decimal("0"))

    # Close prices (for P&L calculation)
    long_close_price: Mapped[Optional[Decimal]] = mapped_column(Numeric(20, 8))
    short_close_price: Mapped[Optional[Decimal]] = mapped_column(Numeric(20, 8))

    # Notes/metadata
    notes: Mapped[Optional[str]] = mapped_column(Text)

    # Relationships
    trades: Mapped[List["Trade"]] = relationship(
        "Trade", back_populates="position", cascade="all, delete-orphan"
    )
    funding_events: Mapped[List["FundingEvent"]] = relationship(
        "FundingEvent", back_populates="position", cascade="all, delete-orphan"
    )

    # Indexes
    __table_args__ = (
        Index("ix_positions_pair_status", "pair", "status"),
        Index("ix_positions_entry_timestamp", "entry_timestamp"),
    )

    def __repr__(self) -> str:
        return (
            f"<Position(id={self.id[:8]}, pair={self.pair}, "
            f"long={self.long_exchange}, short={self.short_exchange}, "
            f"status={self.status.value})>"
        )

    @property
    def is_open(self) -> bool:
        return self.status == PositionStatus.OPEN

    def calculate_unrealized_pnl(
        self,
        long_current_price: Decimal,
        short_current_price: Decimal,
    ) -> Decimal:
        """Calculate unrealized P&L based on current prices."""
        if not self.long_entry_price or not self.short_entry_price:
            return Decimal("0")

        if not self.long_size or not self.short_size:
            return Decimal("0")

        long_pnl = (long_current_price - self.long_entry_price) * self.long_size
        short_pnl = (self.short_entry_price - short_current_price) * self.short_size

        return long_pnl + short_pnl + self.funding_collected - self.total_fees


class Trade(Base):
    """
    Represents a single order execution.

    Each position has multiple trades (open long, open short, close long, close short).
    """
    __tablename__ = "trades"

    # Primary key
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=generate_uuid)

    # Foreign key to position
    position_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("positions.id"), nullable=False, index=True
    )

    # Trade details
    exchange: Mapped[str] = mapped_column(String(20), nullable=False)
    pair: Mapped[str] = mapped_column(String(20), nullable=False)
    side: Mapped[OrderSide] = mapped_column(Enum(OrderSide), nullable=False)
    action: Mapped[OrderAction] = mapped_column(Enum(OrderAction), nullable=False)
    order_type: Mapped[OrderType] = mapped_column(Enum(OrderType), nullable=False)

    # Execution details
    price: Mapped[Optional[Decimal]] = mapped_column(Numeric(20, 8))
    size: Mapped[Decimal] = mapped_column(Numeric(20, 8), nullable=False)
    fee: Mapped[Decimal] = mapped_column(Numeric(20, 8), default=Decimal("0"))

    # Exchange order ID
    order_id: Mapped[Optional[str]] = mapped_column(String(100))

    # Status
    status: Mapped[TradeStatus] = mapped_column(
        Enum(TradeStatus), default=TradeStatus.PENDING
    )

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now
    )
    executed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))

    # Latency tracking (ms)
    latency_ms: Mapped[Optional[int]] = mapped_column(Integer)

    # Error message if failed
    error_message: Mapped[Optional[str]] = mapped_column(Text)

    # Relationship
    position: Mapped["Position"] = relationship("Position", back_populates="trades")

    # Indexes
    __table_args__ = (
        Index("ix_trades_exchange_order_id", "exchange", "order_id"),
        Index("ix_trades_created_at", "created_at"),
    )

    def __repr__(self) -> str:
        return (
            f"<Trade(id={self.id[:8]}, exchange={self.exchange}, "
            f"side={self.side.value}, action={self.action.value}, "
            f"status={self.status.value})>"
        )


class FundingEvent(Base):
    """
    Records funding payments received for a position.

    Funding is typically paid every 8 hours on most exchanges.
    """
    __tablename__ = "funding_events"

    # Primary key
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=generate_uuid)

    # Foreign key to position
    position_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("positions.id"), nullable=False, index=True
    )

    # Funding details
    exchange: Mapped[str] = mapped_column(String(20), nullable=False)
    pair: Mapped[str] = mapped_column(String(20), nullable=False)
    side: Mapped[OrderSide] = mapped_column(Enum(OrderSide), nullable=False)

    # Funding rate and payment
    funding_rate: Mapped[Decimal] = mapped_column(Numeric(12, 10), nullable=False)
    payment_usd: Mapped[Decimal] = mapped_column(Numeric(20, 8), nullable=False)

    # Position size at funding time
    position_size: Mapped[Decimal] = mapped_column(Numeric(20, 8), nullable=False)

    # Timestamp
    timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, index=True
    )

    # Relationship
    position: Mapped["Position"] = relationship("Position", back_populates="funding_events")

    def __repr__(self) -> str:
        return (
            f"<FundingEvent(id={self.id[:8]}, exchange={self.exchange}, "
            f"rate={self.funding_rate}, payment=${self.payment_usd})>"
        )


class SystemState(Base):
    """
    Stores system state for checkpoint and recovery.

    Used to persist engine state across restarts.
    """
    __tablename__ = "system_state"

    # Key-value storage
    key: Mapped[str] = mapped_column(String(100), primary_key=True)
    value: Mapped[str] = mapped_column(Text, nullable=False)

    # Timestamp
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )

    def __repr__(self) -> str:
        return f"<SystemState(key={self.key})>"
