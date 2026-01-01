"""
Unit tests for execution engine module.
"""

from datetime import datetime, timezone, timedelta
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.config.schema import TradingConfig, LeverageConfig
from backend.engine.executor import ExecutionEngine, ExecutionResult
from backend.engine.detector import ArbitrageOpportunity
from backend.exchanges.base import CircuitBreakerOpenError
from backend.exchanges.types import (
    OrderBook,
    OrderBookLevel,
    OrderResult,
    OrderSide,
    OrderType,
    OrderStatus,
)


class TestExecutionResult:
    """Tests for ExecutionResult dataclass."""

    def test_successful_result(self):
        """Test creating a successful execution result."""
        long_order = MagicMock(spec=OrderResult)
        short_order = MagicMock(spec=OrderResult)

        result = ExecutionResult(
            success=True,
            long_order=long_order,
            short_order=short_order,
            execution_time_ms=150,
        )

        assert result.success is True
        assert result.long_order == long_order
        assert result.short_order == short_order
        assert result.error_message is None
        assert result.execution_time_ms == 150

    def test_failed_result(self):
        """Test creating a failed execution result."""
        result = ExecutionResult(
            success=False,
            long_order=None,
            short_order=None,
            error_message="Order failed",
            execution_time_ms=50,
        )

        assert result.success is False
        assert result.long_order is None
        assert result.short_order is None
        assert result.error_message == "Order failed"


class TestExecutionEngine:
    """Tests for ExecutionEngine."""

    @pytest.fixture
    def trading_config(self) -> TradingConfig:
        """Create trading config for tests."""
        return TradingConfig(
            symbols=["BTC/USDT:USDT", "ETH/USDT:USDT"],
            min_daily_spread_base=Decimal("0.0003"),  # Daily normalized
            min_daily_spread_per_10k=Decimal("0.00003"),  # Daily normalized
            entry_buffer_minutes=20,
            order_fill_timeout_seconds=5,  # Short timeout for tests
            max_position_per_pair_usd=Decimal("50000"),
            simulation_mode=True,
            leverage={
                "binance": LeverageConfig(default=5),
                "bybit": LeverageConfig(default=5),
            },
        )

    @pytest.fixture
    def mock_orderbook(self):
        """Create a mock orderbook."""
        return OrderBook(
            exchange="binance",
            symbol="BTC/USDT:USDT",
            bids=[
                OrderBookLevel(price=Decimal("50000"), size=Decimal("1.0")),
                OrderBookLevel(price=Decimal("49990"), size=Decimal("2.0")),
            ],
            asks=[
                OrderBookLevel(price=Decimal("50010"), size=Decimal("1.0")),
                OrderBookLevel(price=Decimal("50020"), size=Decimal("2.0")),
            ],
            timestamp=datetime.now(timezone.utc),
        )

    @pytest.fixture
    def mock_order_result(self):
        """Create a mock filled order result."""
        return OrderResult(
            order_id="order-123",
            client_order_id=None,
            exchange="binance",
            symbol="BTC/USDT:USDT",
            side=OrderSide.BUY,
            order_type=OrderType.LIMIT,
            status=OrderStatus.FILLED,
            size=Decimal("0.2"),
            filled_size=Decimal("0.2"),
            price=Decimal("50005"),
            average_price=Decimal("50005"),
            fee=Decimal("4.00"),
            fee_currency="USDT",
            timestamp=datetime.now(timezone.utc),
        )

    @pytest.fixture
    def mock_exchanges(self, mock_orderbook, mock_order_result):
        """Create mock exchange adapters."""
        binance = MagicMock()
        binance.name = "binance"
        binance.get_orderbook = AsyncMock(return_value=mock_orderbook)
        binance.place_order = AsyncMock(return_value=mock_order_result)
        binance.get_order = AsyncMock(return_value=mock_order_result)
        binance.cancel_order = AsyncMock()
        binance.set_leverage = AsyncMock()

        bybit = MagicMock()
        bybit.name = "bybit"
        bybit.get_orderbook = AsyncMock(return_value=OrderBook(
            exchange="bybit",
            symbol="BTC/USDT:USDT",
            bids=[
                OrderBookLevel(price=Decimal("50000"), size=Decimal("0.5")),
                OrderBookLevel(price=Decimal("49990"), size=Decimal("1.0")),
            ],
            asks=[
                OrderBookLevel(price=Decimal("50010"), size=Decimal("0.5")),
                OrderBookLevel(price=Decimal("50020"), size=Decimal("1.0")),
            ],
            timestamp=datetime.now(timezone.utc),
        ))
        bybit.place_order = AsyncMock(return_value=OrderResult(
            order_id="order-456",
            client_order_id=None,
            exchange="bybit",
            symbol="BTC/USDT:USDT",
            side=OrderSide.SELL,
            order_type=OrderType.LIMIT,
            status=OrderStatus.FILLED,
            size=Decimal("0.2"),
            filled_size=Decimal("0.2"),
            price=Decimal("50005"),
            average_price=Decimal("50005"),
            fee=Decimal("4.00"),
            fee_currency="USDT",
            timestamp=datetime.now(timezone.utc),
        ))
        bybit.get_order = AsyncMock()
        bybit.cancel_order = AsyncMock()
        bybit.set_leverage = AsyncMock()

        return {"binance": binance, "bybit": bybit}

    @pytest.fixture
    def executor(self, mock_exchanges, trading_config) -> ExecutionEngine:
        """Create executor instance."""
        return ExecutionEngine(mock_exchanges, trading_config)

    @pytest.fixture
    def opportunity(self):
        """Create a sample opportunity with daily normalized rates."""
        now = datetime.now(timezone.utc)
        # Raw rates: long=-0.0001 (8h), short=0.0003 (8h)
        # Daily rates: long=-0.0003, short=0.0009
        # Daily spread: 0.0012
        return ArbitrageOpportunity(
            symbol="BTC/USDT:USDT",
            long_exchange="bybit",
            short_exchange="binance",
            long_interval_hours=8,
            short_interval_hours=8,
            long_rate=Decimal("-0.0001"),
            short_rate=Decimal("0.0003"),
            long_daily_rate=Decimal("-0.0003"),
            short_daily_rate=Decimal("0.0009"),
            daily_spread=Decimal("0.0012"),
            spread=Decimal("0.0004"),  # Raw spread for backwards compat
            expected_daily_profit=Decimal("12.00"),
            annualized_apr=Decimal("43.8"),
            next_funding_time=now + timedelta(hours=4),
            seconds_to_funding=14400.0,
            detected_at=now,
        )

    def test_init(self, executor, mock_exchanges, trading_config):
        """Test executor initialization."""
        assert executor.exchanges == mock_exchanges
        assert executor.config == trading_config
        assert executor._pending_orders == {}

    def test_pending_orders_count(self, executor):
        """Test pending orders count property."""
        assert executor.pending_orders_count == 0

        executor._pending_orders["order-1"] = MagicMock()
        executor._pending_orders["order-2"] = MagicMock()

        assert executor.pending_orders_count == 2

    @pytest.mark.asyncio
    async def test_execute_entry_success(self, executor, opportunity, mock_exchanges):
        """Test successful entry execution."""
        result = await executor.execute_entry(opportunity, Decimal("10000"))

        assert result.success is True
        assert result.long_order is not None
        assert result.short_order is not None
        assert result.error_message is None
        assert result.execution_time_ms >= 0

        # Should set leverage on both exchanges
        mock_exchanges["binance"].set_leverage.assert_called()
        mock_exchanges["bybit"].set_leverage.assert_called()

    @pytest.mark.asyncio
    async def test_execute_entry_first_leg_fails(self, executor, opportunity, mock_exchanges):
        """Test entry when first leg fails to fill."""
        # First order doesn't fill - bybit is first because it has lower liquidity
        unfilled_result = OrderResult(
            order_id="order-123",
            client_order_id=None,
            exchange="bybit",
            symbol="BTC/USDT:USDT",
            side=OrderSide.BUY,
            order_type=OrderType.LIMIT,
            status=OrderStatus.CANCELLED,
            size=Decimal("0.2"),
            filled_size=Decimal("0"),
            price=Decimal("50005"),
            average_price=None,
            fee=Decimal("0"),
            fee_currency="USDT",
            timestamp=datetime.now(timezone.utc),
        )
        # Place order returns unfilled, get_order also returns unfilled
        mock_exchanges["bybit"].place_order.return_value = unfilled_result
        mock_exchanges["bybit"].get_order.return_value = unfilled_result

        result = await executor.execute_entry(opportunity, Decimal("10000"))

        assert result.success is False
        assert "First leg failed" in result.error_message

    @pytest.mark.asyncio
    async def test_execute_entry_second_leg_fails_closes_first(
        self, executor, opportunity, mock_exchanges
    ):
        """Test that second leg failure closes first leg."""
        # First leg (bybit) succeeds with filled order
        filled_result = OrderResult(
            order_id="order-123",
            client_order_id=None,
            exchange="bybit",
            symbol="BTC/USDT:USDT",
            side=OrderSide.BUY,
            order_type=OrderType.LIMIT,
            status=OrderStatus.FILLED,
            size=Decimal("0.2"),
            filled_size=Decimal("0.2"),
            price=Decimal("50005"),
            average_price=Decimal("50005"),
            fee=Decimal("4.00"),
            fee_currency="USDT",
            timestamp=datetime.now(timezone.utc),
        )

        # Second leg (binance) fails
        unfilled_result = OrderResult(
            order_id="order-456",
            client_order_id=None,
            exchange="binance",
            symbol="BTC/USDT:USDT",
            side=OrderSide.SELL,
            order_type=OrderType.LIMIT,
            status=OrderStatus.CANCELLED,
            size=Decimal("0.2"),
            filled_size=Decimal("0"),
            price=Decimal("50005"),
            average_price=None,
            fee=Decimal("0"),
            fee_currency="USDT",
            timestamp=datetime.now(timezone.utc),
        )

        # bybit succeeds (first leg), binance fails (second leg)
        mock_exchanges["bybit"].place_order.return_value = filled_result
        mock_exchanges["binance"].place_order.return_value = unfilled_result
        mock_exchanges["binance"].get_order.return_value = unfilled_result

        result = await executor.execute_entry(opportunity, Decimal("10000"))

        assert result.success is False
        assert "Second leg failed" in result.error_message

    @pytest.mark.asyncio
    async def test_execute_entry_circuit_breaker(self, executor, opportunity, mock_exchanges):
        """Test entry when circuit breaker is open."""
        mock_exchanges["bybit"].get_orderbook.side_effect = CircuitBreakerOpenError("Too many failures")

        result = await executor.execute_entry(opportunity, Decimal("10000"))

        assert result.success is False
        assert "Circuit breaker" in result.error_message

    @pytest.mark.asyncio
    async def test_execute_entry_general_exception(self, executor, opportunity, mock_exchanges):
        """Test entry handles general exceptions."""
        mock_exchanges["bybit"].get_orderbook.side_effect = Exception("Network error")

        result = await executor.execute_entry(opportunity, Decimal("10000"))

        assert result.success is False
        assert "Network error" in result.error_message

    @pytest.mark.asyncio
    async def test_execute_exit_success(self, executor, mock_exchanges):
        """Test successful exit execution."""
        result = await executor.execute_exit(
            symbol="BTC/USDT:USDT",
            long_exchange="bybit",
            short_exchange="binance",
            long_size=Decimal("0.2"),
            short_size=Decimal("0.2"),
        )

        assert result.success is True
        assert result.long_order is not None
        assert result.short_order is not None

        # Both exchanges should have place_order called (for closing)
        mock_exchanges["binance"].place_order.assert_called()
        mock_exchanges["bybit"].place_order.assert_called()

    @pytest.mark.asyncio
    async def test_execute_exit_one_leg_fails(self, executor, mock_exchanges):
        """Test exit when one leg fails."""
        mock_exchanges["binance"].place_order.side_effect = Exception("Order failed")

        result = await executor.execute_exit(
            symbol="BTC/USDT:USDT",
            long_exchange="bybit",
            short_exchange="binance",
            long_size=Decimal("0.2"),
            short_size=Decimal("0.2"),
        )

        assert result.success is False
        # One order should still have succeeded
        assert result.long_order is not None or result.short_order is not None

    @pytest.mark.asyncio
    async def test_execute_exit_both_legs_fail(self, executor, mock_exchanges):
        """Test exit when both legs fail."""
        mock_exchanges["binance"].place_order.side_effect = Exception("Order failed")
        mock_exchanges["bybit"].place_order.side_effect = Exception("Order failed")

        result = await executor.execute_exit(
            symbol="BTC/USDT:USDT",
            long_exchange="bybit",
            short_exchange="binance",
            long_size=Decimal("0.2"),
            short_size=Decimal("0.2"),
        )

        assert result.success is False
        assert result.long_order is None
        assert result.short_order is None

    @pytest.mark.asyncio
    async def test_set_leverage_error_handled(self, executor, opportunity, mock_exchanges):
        """Test that leverage setting errors are handled gracefully."""
        mock_exchanges["binance"].set_leverage.side_effect = Exception("Leverage error")

        # Should not raise
        result = await executor.execute_entry(opportunity, Decimal("10000"))

        # Execution should continue despite leverage error
        assert result.success is True

    @pytest.mark.asyncio
    async def test_execute_with_timeout_immediate_fill(self, executor, mock_exchanges):
        """Test order that fills immediately."""
        filled_result = OrderResult(
            order_id="order-123",
            client_order_id=None,
            exchange="binance",
            symbol="BTC/USDT:USDT",
            side=OrderSide.BUY,
            order_type=OrderType.LIMIT,
            status=OrderStatus.FILLED,
            size=Decimal("0.2"),
            filled_size=Decimal("0.2"),
            price=Decimal("50005"),
            average_price=Decimal("50005"),
            fee=Decimal("4.00"),
            fee_currency="USDT",
            timestamp=datetime.now(timezone.utc),
        )
        mock_exchanges["binance"].place_order.return_value = filled_result

        result = await executor._execute_with_timeout(
            "binance",
            "BTC/USDT:USDT",
            OrderSide.BUY,
            Decimal("0.2"),
            Decimal("50005"),
        )

        assert result is not None
        assert result.is_filled

    @pytest.mark.asyncio
    async def test_close_position_market_order(self, executor, mock_exchanges):
        """Test closing position with market order."""
        await executor._close_position(
            "binance",
            "BTC/USDT:USDT",
            OrderSide.SELL,
            Decimal("0.2"),
        )

        # Should place a market order with reduce_only
        call_args = mock_exchanges["binance"].place_order.call_args
        order = call_args[0][0]
        assert order.order_type == OrderType.MARKET
        assert order.reduce_only is True
        assert order.side == OrderSide.SELL

    @pytest.mark.asyncio
    async def test_emergency_close(self, executor, mock_exchanges):
        """Test emergency close of position."""
        await executor._emergency_close(
            "binance",
            "BTC/USDT:USDT",
            OrderSide.BUY,
            Decimal("0.2"),
        )

        # Should close with opposite side (SELL)
        call_args = mock_exchanges["binance"].place_order.call_args
        order = call_args[0][0]
        assert order.side == OrderSide.SELL

    @pytest.mark.asyncio
    async def test_emergency_close_handles_errors(self, executor, mock_exchanges):
        """Test emergency close handles errors gracefully."""
        mock_exchanges["binance"].place_order.side_effect = Exception("Order failed")

        # Should not raise
        await executor._emergency_close(
            "binance",
            "BTC/USDT:USDT",
            OrderSide.BUY,
            Decimal("0.2"),
        )

    @pytest.mark.asyncio
    async def test_execution_order_lower_liquidity_first(
        self, executor, opportunity, mock_exchanges
    ):
        """Test that lower liquidity exchange executes first."""
        # bybit has lower ask depth (0.5 + 1.0 = 1.5) vs binance (1.0 + 2.0 = 3.0)
        # So bybit (long) should execute first since we're buying on asks

        call_order = []

        async def track_binance_call(*args, **kwargs):
            call_order.append("binance")
            return mock_exchanges["binance"].place_order.return_value

        async def track_bybit_call(*args, **kwargs):
            call_order.append("bybit")
            return mock_exchanges["bybit"].place_order.return_value

        mock_exchanges["binance"].place_order = AsyncMock(side_effect=track_binance_call)
        mock_exchanges["bybit"].place_order = AsyncMock(side_effect=track_bybit_call)

        await executor.execute_entry(opportunity, Decimal("10000"))

        # bybit should be called first (lower liquidity)
        assert call_order[0] == "bybit"
