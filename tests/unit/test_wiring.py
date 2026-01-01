"""
Tests for wiring between components.

These tests verify that:
1. WebSocket broadcasts are called from coordinator
2. Position callbacks are invoked correctly
3. Engine control endpoints work with coordinator
4. Health check uses real status
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime, timezone, timedelta
from decimal import Decimal

from backend.engine.coordinator import TradingCoordinator, EngineState, get_ws_manager
from backend.engine.detector import ArbitrageOpportunity
from backend.engine.executor import ExecutionResult, OrderResult
from backend.config.schema import TradingConfig
from backend.exchanges.types import FundingRate


@pytest.fixture
def trading_config():
    """Create a test trading config."""
    return TradingConfig(
        symbols=["BTC/USDT:USDT"],
        max_position_per_pair_usd=Decimal("10000"),
        min_daily_spread_base=Decimal("0.0003"),
        entry_buffer_minutes=5,
    )


@pytest.fixture
def mock_exchanges():
    """Create mock exchange adapters."""
    binance = MagicMock()
    binance.is_connected = True
    binance.name = "binance"
    binance.get_funding_rates = AsyncMock(return_value={})
    binance.subscribe_funding_rates = AsyncMock()

    bybit = MagicMock()
    bybit.is_connected = True
    bybit.name = "bybit"
    bybit.get_funding_rates = AsyncMock(return_value={})
    bybit.subscribe_funding_rates = AsyncMock()

    return {"binance": binance, "bybit": bybit}


@pytest.fixture
def coordinator(trading_config, mock_exchanges):
    """Create a test coordinator."""
    return TradingCoordinator(
        config=trading_config,
        exchanges=mock_exchanges,
    )


class TestWebSocketBroadcasts:
    """Tests for WebSocket broadcast wiring."""

    @pytest.mark.asyncio
    async def test_broadcast_position_update(self, coordinator):
        """Test that position update is broadcast via WebSocket."""
        with patch('backend.engine.coordinator.get_ws_manager') as mock_get_ws:
            mock_ws = MagicMock()
            mock_ws.send_position_update = AsyncMock()
            mock_get_ws.return_value = mock_ws

            await coordinator._broadcast_position_update(
                position_id="test-123",
                status="OPEN",
                unrealized_pnl=100.0,
                funding_collected=5.0,
            )

            mock_ws.send_position_update.assert_called_once_with(
                position_id="test-123",
                status="OPEN",
                unrealized_pnl=100.0,
                funding_collected=5.0,
            )

    @pytest.mark.asyncio
    async def test_broadcast_trade_executed(self, coordinator):
        """Test that trade execution is broadcast via WebSocket."""
        with patch('backend.engine.coordinator.get_ws_manager') as mock_get_ws:
            mock_ws = MagicMock()
            mock_ws.send_trade_executed = AsyncMock()
            mock_get_ws.return_value = mock_ws

            await coordinator._broadcast_trade_executed(
                position_id="test-123",
                exchange="binance",
                side="BUY",
                price=50000.0,
                size=0.1,
                fee=5.0,
            )

            mock_ws.send_trade_executed.assert_called_once_with(
                position_id="test-123",
                exchange="binance",
                side="BUY",
                price=50000.0,
                size=0.1,
                fee=5.0,
            )

    @pytest.mark.asyncio
    async def test_broadcast_opportunity(self, coordinator):
        """Test that opportunity is broadcast via WebSocket."""
        with patch('backend.engine.coordinator.get_ws_manager') as mock_get_ws:
            mock_ws = MagicMock()
            mock_ws.send_opportunity = AsyncMock()
            mock_get_ws.return_value = mock_ws

            await coordinator._broadcast_opportunity(
                symbol="BTC/USDT:USDT",
                long_exchange="binance",
                short_exchange="bybit",
                spread=0.001,
                expected_profit=10.0,
            )

            mock_ws.send_opportunity.assert_called_once_with(
                symbol="BTC/USDT:USDT",
                long_exchange="binance",
                short_exchange="bybit",
                spread=0.001,
                expected_profit=10.0,
            )

    @pytest.mark.asyncio
    async def test_broadcast_engine_status(self, coordinator):
        """Test that engine status is broadcast via WebSocket."""
        with patch('backend.engine.coordinator.get_ws_manager') as mock_get_ws:
            mock_ws = MagicMock()
            mock_ws.send_engine_status = AsyncMock()
            mock_get_ws.return_value = mock_ws

            coordinator._state = EngineState.RUNNING
            await coordinator._broadcast_engine_status()

            mock_ws.send_engine_status.assert_called_once()
            call_kwargs = mock_ws.send_engine_status.call_args[1]
            assert call_kwargs["status"] == "RUNNING"

    @pytest.mark.asyncio
    async def test_broadcast_handles_errors_gracefully(self, coordinator):
        """Test that broadcast errors don't crash the coordinator."""
        with patch('backend.engine.coordinator.get_ws_manager') as mock_get_ws:
            mock_ws = MagicMock()
            mock_ws.send_position_update = AsyncMock(side_effect=Exception("WS Error"))
            mock_get_ws.return_value = mock_ws

            # Should not raise
            await coordinator._broadcast_position_update(
                position_id="test-123",
                status="OPEN",
                unrealized_pnl=100.0,
                funding_collected=5.0,
            )


class TestPositionCallbacks:
    """Tests for position event callbacks."""

    @pytest.mark.asyncio
    async def test_on_position_closed_callback_invoked(self, coordinator, mock_exchanges):
        """Test that position closed callbacks are invoked."""
        callback = AsyncMock()
        coordinator.on_position_closed(callback)

        # Mock the position manager and executor
        mock_position = MagicMock()
        mock_position.id = "test-123"
        mock_position.pair = "BTC/USDT:USDT"
        mock_position.is_open = True
        mock_position.long_exchange = "binance"
        mock_position.short_exchange = "bybit"
        mock_position.long_size = Decimal("0.1")
        mock_position.short_size = Decimal("0.1")

        mock_closed_position = MagicMock()
        mock_closed_position.funding_collected = Decimal("10.0")
        mock_closed_position.realized_pnl = Decimal("50.0")

        with patch('backend.engine.coordinator.get_session') as mock_session:
            mock_ctx = AsyncMock()
            mock_session.return_value.__aenter__ = AsyncMock(return_value=mock_ctx)
            mock_session.return_value.__aexit__ = AsyncMock(return_value=None)

            with patch('backend.engine.coordinator.PositionManager') as mock_pm_class:
                mock_pm = MagicMock()
                mock_pm.get_position = AsyncMock(return_value=mock_position)
                mock_pm.close_position = AsyncMock(return_value=mock_closed_position)
                mock_pm_class.return_value = mock_pm

                # Mock executor success
                coordinator.executor.execute_exit = AsyncMock(return_value=ExecutionResult(
                    success=True,
                    long_order=None,
                    short_order=None,
                ))

                with patch('backend.engine.coordinator.get_ws_manager') as mock_get_ws:
                    mock_ws = MagicMock()
                    mock_ws.send_position_update = AsyncMock()
                    mock_ws.send_trade_executed = AsyncMock()
                    mock_ws.send_alert = AsyncMock()
                    mock_get_ws.return_value = mock_ws

                    result = await coordinator.close_position("test-123", "manual")

                    assert result is True
                    callback.assert_called_once()
                    # Verify callback was called with position and reason
                    call_args = callback.call_args[0]
                    assert call_args[1] == "manual"

    @pytest.mark.asyncio
    async def test_on_position_opened_callback_registered(self, coordinator):
        """Test that position opened callbacks can be registered."""
        callback = AsyncMock()
        coordinator.on_position_opened(callback)

        assert callback in coordinator._on_position_opened


class TestEngineStatusBroadcast:
    """Tests for engine status broadcasts on start/stop."""

    @pytest.mark.asyncio
    async def test_start_broadcasts_status(self, coordinator):
        """Test that starting engine broadcasts status."""
        with patch('backend.engine.coordinator.get_ws_manager') as mock_get_ws:
            mock_ws = MagicMock()
            mock_ws.send_engine_status = AsyncMock()
            mock_get_ws.return_value = mock_ws

            await coordinator.start()

            # Should broadcast engine status
            mock_ws.send_engine_status.assert_called()

            await coordinator.stop()

    @pytest.mark.asyncio
    async def test_stop_broadcasts_status(self, coordinator):
        """Test that stopping engine broadcasts status."""
        with patch('backend.engine.coordinator.get_ws_manager') as mock_get_ws:
            mock_ws = MagicMock()
            mock_ws.send_engine_status = AsyncMock()
            mock_get_ws.return_value = mock_ws

            await coordinator.start()
            mock_ws.send_engine_status.reset_mock()

            await coordinator.stop()

            # Should broadcast engine status on stop
            mock_ws.send_engine_status.assert_called()


class TestFundingRateBroadcast:
    """Tests for funding rate broadcasts."""

    @pytest.mark.asyncio
    async def test_rates_update_triggers_broadcast(self, coordinator):
        """Test that rate updates trigger WebSocket broadcasts."""
        now = datetime.now(timezone.utc)

        rates = {
            "binance": {
                "BTC/USDT:USDT": FundingRate(
                    exchange="binance",
                    symbol="BTC/USDT:USDT",
                    rate=Decimal("0.0001"),
                    predicted_rate=Decimal("0.00015"),
                    next_funding_time=now + timedelta(hours=4),
                    timestamp=now,
                    interval_hours=8,
                )
            }
        }

        with patch('backend.engine.coordinator.get_ws_manager') as mock_get_ws:
            mock_ws = MagicMock()
            mock_ws.send_funding_rate_update = AsyncMock()
            mock_get_ws.return_value = mock_ws

            await coordinator._broadcast_rates(rates)

            mock_ws.send_funding_rate_update.assert_called_once()


class TestCoordinatorGetStatus:
    """Tests for coordinator status retrieval."""

    def test_get_status_returns_correct_state(self, coordinator):
        """Test that get_status returns correct engine state."""
        status = coordinator.get_status()

        assert status.state == EngineState.STOPPED
        assert status.simulation_mode is True
        assert "binance" in status.connected_exchanges
        assert "bybit" in status.connected_exchanges

    def test_get_status_shows_kill_switch(self, coordinator):
        """Test that get_status shows kill switch status."""
        coordinator.risk_manager._kill_switch_active = True

        status = coordinator.get_status()

        assert status.kill_switch_active is True


class TestLiquidationCheck:
    """Tests for liquidation checking in funding loop."""

    @pytest.mark.asyncio
    async def test_check_liquidations_called(self, coordinator, mock_exchanges):
        """Test that liquidation check is called for open positions."""
        mock_position = MagicMock()
        mock_position.id = "test-123"
        mock_position.pair = "BTC/USDT:USDT"
        mock_position.long_exchange = "binance"
        mock_position.short_exchange = "bybit"

        coordinator.risk_manager.check_for_liquidation = AsyncMock(return_value=False)

        with patch('backend.engine.coordinator.get_ws_manager') as mock_get_ws:
            mock_ws = MagicMock()
            mock_get_ws.return_value = mock_ws

            mock_pm = MagicMock()
            await coordinator._check_liquidations(mock_pm, [mock_position])

            coordinator.risk_manager.check_for_liquidation.assert_called_once_with(
                "BTC/USDT:USDT",
                "binance",
                "bybit",
            )

    @pytest.mark.asyncio
    async def test_check_liquidations_alerts_on_detection(self, coordinator, mock_exchanges):
        """Test that liquidation detection triggers alert."""
        mock_position = MagicMock()
        mock_position.id = "test-123"
        mock_position.pair = "BTC/USDT:USDT"
        mock_position.long_exchange = "binance"
        mock_position.short_exchange = "bybit"

        coordinator.risk_manager.check_for_liquidation = AsyncMock(return_value=True)
        coordinator._send_alert = AsyncMock()

        with patch('backend.engine.coordinator.get_ws_manager') as mock_get_ws:
            mock_ws = MagicMock()
            mock_ws.send_alert = AsyncMock()
            mock_get_ws.return_value = mock_ws

            mock_pm = MagicMock()
            await coordinator._check_liquidations(mock_pm, [mock_position])

            coordinator._send_alert.assert_called_once()
            assert "Liquidation" in coordinator._send_alert.call_args[0][1]


class TestWebSocketManagerLazyImport:
    """Tests for lazy WebSocket manager import."""

    def test_get_ws_manager_returns_manager(self):
        """Test that get_ws_manager returns the WebSocket manager."""
        # Reset the cached manager
        import backend.engine.coordinator as coord_module
        coord_module._ws_manager = None

        ws = get_ws_manager()

        assert ws is not None
        # Should be the same instance on subsequent calls
        assert get_ws_manager() is ws
