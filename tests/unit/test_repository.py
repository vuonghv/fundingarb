"""
Unit tests for database repository module.
"""

from datetime import datetime, timezone
from decimal import Decimal

import pytest
import pytest_asyncio

from backend.database.models import Position, Trade, FundingEvent, PositionStatus, OrderSide, OrderAction, OrderType, TradeStatus
from backend.database.repository import PositionRepository, TradeRepository, FundingEventRepository


class TestPositionRepository:
    """Tests for PositionRepository."""

    @pytest_asyncio.fixture
    async def position(self, async_session, sample_position):
        """Create a test position."""
        pos = Position(**sample_position)
        async_session.add(pos)
        await async_session.commit()
        return pos

    @pytest.mark.asyncio
    async def test_create_position(self, position_repo, sample_position):
        """Test creating a new position."""
        position = await position_repo.create(Position(**sample_position))
        assert position.id == sample_position["id"]
        assert position.pair == sample_position["pair"]
        assert position.status == PositionStatus.OPEN

    @pytest.mark.asyncio
    async def test_get_by_id(self, async_session, position_repo, sample_position):
        """Test getting position by ID."""
        # Create position
        pos = Position(**sample_position)
        async_session.add(pos)
        await async_session.commit()

        # Retrieve
        retrieved = await position_repo.get_by_id(sample_position["id"])
        assert retrieved is not None
        assert retrieved.id == sample_position["id"]

    @pytest.mark.asyncio
    async def test_get_by_id_not_found(self, position_repo):
        """Test getting non-existent position."""
        retrieved = await position_repo.get_by_id("nonexistent-id")
        assert retrieved is None

    @pytest.mark.asyncio
    async def test_get_open_positions(self, async_session, position_repo):
        """Test getting open positions."""
        # Create open position
        open_pos = Position(
            id="open-pos-1",
            pair="BTC/USDT:USDT",
            long_exchange="bybit",
            short_exchange="binance",
            size_usd=Decimal("10000"),
            status=PositionStatus.OPEN,
        )
        async_session.add(open_pos)

        # Create closed position
        closed_pos = Position(
            id="closed-pos-1",
            pair="ETH/USDT:USDT",
            long_exchange="bybit",
            short_exchange="binance",
            size_usd=Decimal("5000"),
            status=PositionStatus.CLOSED,
        )
        async_session.add(closed_pos)
        await async_session.commit()

        open_positions = await position_repo.get_open_positions()
        assert len(open_positions) == 1
        assert open_positions[0].id == "open-pos-1"

    @pytest.mark.asyncio
    async def test_get_closed_positions(self, async_session, position_repo):
        """Test getting closed positions with pagination."""
        # Create multiple closed positions
        for i in range(5):
            pos = Position(
                id=f"closed-pos-{i}",
                pair="BTC/USDT:USDT",
                long_exchange="bybit",
                short_exchange="binance",
                size_usd=Decimal("10000"),
                status=PositionStatus.CLOSED,
            )
            async_session.add(pos)
        await async_session.commit()

        # Get with limit
        closed = await position_repo.get_closed_positions(limit=3)
        assert len(closed) == 3

        # Get with offset
        closed_offset = await position_repo.get_closed_positions(limit=3, offset=3)
        assert len(closed_offset) == 2

    @pytest.mark.asyncio
    async def test_update_position(self, async_session, position_repo, sample_position):
        """Test updating a position."""
        # Create position
        pos = Position(**sample_position)
        async_session.add(pos)
        await async_session.commit()

        # Update using the repository's update method
        await position_repo.update(
            pos.id,
            funding_collected=Decimal("50.00"),
            status=PositionStatus.CLOSED
        )
        await async_session.commit()

        # Fetch the updated position
        updated = await position_repo.get_by_id(pos.id)
        assert float(updated.funding_collected) == 50.00
        assert updated.status == PositionStatus.CLOSED

    @pytest.mark.asyncio
    async def test_count_open_positions(self, async_session, position_repo):
        """Test counting open positions."""
        # Create positions
        for i in range(3):
            pos = Position(
                id=f"open-{i}",
                pair="BTC/USDT:USDT",
                long_exchange="bybit",
                short_exchange="binance",
                size_usd=Decimal("10000"),
                status=PositionStatus.OPEN,
            )
            async_session.add(pos)
        await async_session.commit()

        count = await position_repo.count_open_positions()
        assert count == 3

    @pytest.mark.asyncio
    async def test_get_total_pnl(self, async_session, position_repo):
        """Test getting total realized PnL."""
        # Create closed positions with PnL
        pos1 = Position(
            id="pnl-1",
            pair="BTC/USDT:USDT",
            long_exchange="bybit",
            short_exchange="binance",
            size_usd=Decimal("10000"),
            status=PositionStatus.CLOSED,
            realized_pnl=Decimal("100.00"),
        )
        pos2 = Position(
            id="pnl-2",
            pair="ETH/USDT:USDT",
            long_exchange="bybit",
            short_exchange="binance",
            size_usd=Decimal("5000"),
            status=PositionStatus.CLOSED,
            realized_pnl=Decimal("-50.00"),
        )
        async_session.add_all([pos1, pos2])
        await async_session.commit()

        total = await position_repo.get_total_pnl()
        assert float(total) == 50.00


class TestTradeRepository:
    """Tests for TradeRepository."""

    @pytest.mark.asyncio
    async def test_create_trade(self, async_session):
        """Test creating a trade."""
        repo = TradeRepository(async_session)

        trade = Trade(
            id="trade-001",
            position_id="pos-001",
            exchange="binance",
            pair="BTC/USDT:USDT",
            side=OrderSide.LONG,
            action=OrderAction.OPEN,
            order_type=OrderType.LIMIT,
            price=Decimal("50000.00"),
            size=Decimal("0.1"),
            fee=Decimal("5.00"),
            order_id="order-123",
            status=TradeStatus.FILLED,
        )

        created = await repo.create(trade)
        assert created.id == "trade-001"
        assert created.status == TradeStatus.FILLED

    @pytest.mark.asyncio
    async def test_get_trades_for_position(self, async_session):
        """Test getting trades for a position."""
        repo = TradeRepository(async_session)

        # Create trades
        for i in range(3):
            trade = Trade(
                id=f"trade-{i}",
                position_id="pos-001",
                exchange="binance",
                pair="BTC/USDT:USDT",
                side=OrderSide.LONG,
                action=OrderAction.OPEN,
                order_type=OrderType.MARKET,
                size=Decimal("0.1"),
                fee=Decimal("1.00"),
                status=TradeStatus.FILLED,
            )
            async_session.add(trade)
        await async_session.commit()

        trades = await repo.get_trades_for_position("pos-001")
        assert len(trades) == 3


class TestFundingEventRepository:
    """Tests for FundingEventRepository."""

    @pytest.mark.asyncio
    async def test_create_funding_event(self, async_session):
        """Test creating a funding event."""
        repo = FundingEventRepository(async_session)

        event = FundingEvent(
            id="funding-001",
            position_id="pos-001",
            exchange="binance",
            pair="BTC/USDT:USDT",
            side=OrderSide.SHORT,
            funding_rate=Decimal("0.0001"),
            payment_usd=Decimal("10.00"),
            position_size=Decimal("0.5"),
        )

        created = await repo.create(event)
        assert created.id == "funding-001"
        assert float(created.payment_usd) == 10.00

    @pytest.mark.asyncio
    async def test_get_events_for_position(self, async_session):
        """Test getting funding events for a position."""
        repo = FundingEventRepository(async_session)

        # Create events
        for i in range(5):
            event = FundingEvent(
                id=f"funding-{i}",
                position_id="pos-001",
                exchange="binance",
                pair="BTC/USDT:USDT",
                side=OrderSide.SHORT,
                funding_rate=Decimal("0.0001"),
                payment_usd=Decimal("10.00"),
                position_size=Decimal("0.5"),
            )
            async_session.add(event)
        await async_session.commit()

        events = await repo.get_events_for_position("pos-001")
        assert len(events) == 5
