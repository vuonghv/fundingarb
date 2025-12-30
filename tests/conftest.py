"""
Pytest configuration and fixtures.
"""

import asyncio
import os
import tempfile
from datetime import datetime, timezone
from decimal import Decimal
from typing import AsyncGenerator, Generator
from unittest.mock import AsyncMock, MagicMock

import pytest
import pytest_asyncio
from httpx import AsyncClient, ASGITransport
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker

from backend.config.schema import (
    Config,
    ExchangeConfig,
    TradingConfig,
    DatabaseConfig,
    TelegramConfig,
    APIConfig,
    LeverageConfig,
)
from backend.database.models import Base, Position, Trade, FundingEvent, PositionStatus
from backend.database.repository import PositionRepository
from backend.exchanges.types import FundingRate, OrderBook, Order, OrderResult, OrderSide, OrderType
from backend.api.server import create_app


# ============================================================
# Event Loop Fixture
# ============================================================
@pytest.fixture(scope="session")
def event_loop():
    """Create an event loop for the test session."""
    loop = asyncio.get_event_loop_policy().new_event_loop()
    yield loop
    loop.close()


# ============================================================
# Configuration Fixtures
# ============================================================
@pytest.fixture
def mock_config() -> Config:
    """Create a mock configuration for testing."""
    return Config(
        exchanges={
            "binance": ExchangeConfig(
                api_key="test_key",
                api_secret="test_secret",
                testnet=True,
            ),
            "bybit": ExchangeConfig(
                api_key="test_key",
                api_secret="test_secret",
                testnet=True,
            ),
        },
        trading=TradingConfig(
            symbols=["BTC/USDT:USDT", "ETH/USDT:USDT"],
            min_spread_base=Decimal("0.0001"),
            min_spread_per_10k=Decimal("0.00001"),
            entry_buffer_minutes=20,
            order_fill_timeout_seconds=30,
            max_position_per_pair_usd=Decimal("50000"),
            negative_spread_tolerance=Decimal("-0.0001"),
            leverage={"binance": LeverageConfig(default=5), "bybit": LeverageConfig(default=5)},
            simulation_mode=True,
        ),
        database=DatabaseConfig(
            driver="sqlite",
            sqlite_path=":memory:",
        ),
        telegram=TelegramConfig(
            bot_token="test_token",
            chat_id="test_chat",
            enabled=False,
        ),
        api=APIConfig(
            host="127.0.0.1",
            port=8000,
        ),
    )


# ============================================================
# Database Fixtures
# ============================================================
@pytest_asyncio.fixture
async def async_engine():
    """Create an async SQLite in-memory engine for testing."""
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        echo=False,
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    await engine.dispose()


@pytest_asyncio.fixture
async def async_session(async_engine) -> AsyncGenerator[AsyncSession, None]:
    """Create an async session for testing."""
    async_session_maker = async_sessionmaker(
        async_engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )
    async with async_session_maker() as session:
        yield session


@pytest_asyncio.fixture
async def position_repo(async_session: AsyncSession) -> PositionRepository:
    """Create a position repository for testing."""
    return PositionRepository(async_session)


# ============================================================
# Sample Data Fixtures
# ============================================================
@pytest.fixture
def sample_position() -> dict:
    """Sample position data for testing."""
    return {
        "id": "test-pos-001",
        "pair": "BTC/USDT:USDT",
        "long_exchange": "bybit",
        "short_exchange": "binance",
        "long_entry_price": Decimal("50000.00"),
        "short_entry_price": Decimal("50010.00"),
        "size_usd": Decimal("10000.00"),
        "long_size": Decimal("0.2"),
        "short_size": Decimal("0.2"),
        "leverage_long": 5,
        "leverage_short": 5,
        "entry_timestamp": datetime.now(timezone.utc),
        "entry_funding_spread": Decimal("0.0002"),
        "status": PositionStatus.OPEN,
        "funding_collected": Decimal("0.00"),
        "total_fees": Decimal("10.00"),
    }


@pytest.fixture
def sample_funding_rate() -> FundingRate:
    """Sample funding rate for testing."""
    return FundingRate(
        symbol="BTC/USDT:USDT",
        rate=Decimal("0.0001"),
        next_funding_time=datetime.now(timezone.utc),
        predicted_rate=Decimal("0.00008"),
    )


@pytest.fixture
def sample_order() -> Order:
    """Sample order for testing."""
    return Order(
        symbol="BTC/USDT:USDT",
        side=OrderSide.BUY,
        order_type=OrderType.LIMIT,
        amount=Decimal("0.1"),
        price=Decimal("50000.00"),
        reduce_only=False,
    )


@pytest.fixture
def sample_order_result() -> OrderResult:
    """Sample order result for testing."""
    return OrderResult(
        order_id="order-123",
        symbol="BTC/USDT:USDT",
        side=OrderSide.BUY,
        filled_amount=Decimal("0.1"),
        average_price=Decimal("50000.00"),
        fee=Decimal("5.00"),
        fee_currency="USDT",
        status="closed",
        timestamp=datetime.now(timezone.utc),
    )


# ============================================================
# Mock Exchange Fixtures
# ============================================================
@pytest.fixture
def mock_exchange() -> MagicMock:
    """Create a mock exchange adapter."""
    exchange = MagicMock()
    exchange.name = "mock_exchange"

    # Mock async methods
    exchange.connect = AsyncMock()
    exchange.disconnect = AsyncMock()
    exchange.get_funding_rate = AsyncMock(return_value=FundingRate(
        symbol="BTC/USDT:USDT",
        rate=Decimal("0.0001"),
        next_funding_time=datetime.now(timezone.utc),
    ))
    exchange.get_funding_rates = AsyncMock(return_value=[
        FundingRate(
            symbol="BTC/USDT:USDT",
            rate=Decimal("0.0001"),
            next_funding_time=datetime.now(timezone.utc),
        ),
    ])
    exchange.place_order = AsyncMock(return_value=OrderResult(
        order_id="mock-order-123",
        symbol="BTC/USDT:USDT",
        side=OrderSide.BUY,
        filled_amount=Decimal("0.1"),
        average_price=Decimal("50000.00"),
        fee=Decimal("5.00"),
        fee_currency="USDT",
        status="closed",
        timestamp=datetime.now(timezone.utc),
    ))
    exchange.cancel_order = AsyncMock(return_value=True)
    exchange.get_position = AsyncMock(return_value=None)
    exchange.get_balance = AsyncMock(return_value=Decimal("100000.00"))
    exchange.set_leverage = AsyncMock(return_value=True)
    exchange.get_ticker = AsyncMock(return_value={
        "bid": Decimal("50000.00"),
        "ask": Decimal("50010.00"),
        "last": Decimal("50005.00"),
    })
    exchange.is_healthy = True

    return exchange


# ============================================================
# API Test Fixtures
# ============================================================
@pytest_asyncio.fixture
async def test_app(mock_config: Config):
    """Create a test FastAPI application."""
    import os
    from backend.database.connection import init_database, close_database
    from backend.config.schema import DatabaseConfig

    # Use a temp file for SQLite since in-memory doesn't share across connections
    test_db_path = "/tmp/test_fundingarb.db"
    if os.path.exists(test_db_path):
        os.remove(test_db_path)

    test_db_config = DatabaseConfig(
        driver="sqlite",
        sqlite_path=test_db_path,
    )

    # Initialize global database for API endpoints
    await init_database(test_db_config)

    # Update the config to use the same database
    mock_config.database = test_db_config

    app = create_app(
        config=mock_config,
        coordinator=None,
        exchanges={},
    )

    # Manually set app.state since lifespan events may not run in tests
    app.state.config = mock_config
    app.state.coordinator = None
    app.state.exchanges = {}

    yield app

    # Cleanup
    await close_database()
    if os.path.exists(test_db_path):
        os.remove(test_db_path)


@pytest_asyncio.fixture
async def async_client(test_app) -> AsyncGenerator[AsyncClient, None]:
    """Create an async HTTP client for API testing."""
    transport = ASGITransport(app=test_app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


# ============================================================
# Utility Functions
# ============================================================
def create_test_position(
    session: AsyncSession,
    pair: str = "BTC/USDT:USDT",
    status: PositionStatus = PositionStatus.OPEN,
) -> Position:
    """Helper to create a test position in the database."""
    position = Position(
        id=f"test-{pair}-{status.value}",
        pair=pair,
        long_exchange="bybit",
        short_exchange="binance",
        long_entry_price=Decimal("50000.00"),
        short_entry_price=Decimal("50010.00"),
        size_usd=Decimal("10000.00"),
        long_size=Decimal("0.2"),
        short_size=Decimal("0.2"),
        leverage_long=5,
        leverage_short=5,
        entry_timestamp=datetime.now(timezone.utc),
        entry_funding_spread=Decimal("0.0002"),
        status=status,
        funding_collected=Decimal("0.00"),
        total_fees=Decimal("10.00"),
    )
    return position
