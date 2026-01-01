"""
Unit tests for risk manager module.
"""

from datetime import datetime, timezone, timedelta
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.config.schema import TradingConfig
from backend.engine.risk_manager import RiskManager
from backend.exchanges.types import ExchangePosition, PositionSide, Order, OrderSide, OrderType


class TestRiskManager:
    """Tests for RiskManager."""

    @pytest.fixture
    def trading_config(self) -> TradingConfig:
        """Create trading config for tests."""
        return TradingConfig(
            symbols=["BTC/USDT:USDT", "ETH/USDT:USDT"],
            min_daily_spread_base=Decimal("0.0003"),  # 0.03% daily
            min_daily_spread_per_10k=Decimal("0.00003"),  # 0.003% daily per $10k
            entry_buffer_minutes=20,
            max_position_per_pair_usd=Decimal("50000"),
            simulation_mode=True,
        )

    @pytest.fixture
    def mock_exchanges(self):
        """Create mock exchange adapters."""
        binance = MagicMock()
        binance.name = "binance"
        binance.cancel_all_orders = AsyncMock(return_value=5)
        binance.get_positions = AsyncMock(return_value=[])
        binance.place_order = AsyncMock()

        bybit = MagicMock()
        bybit.name = "bybit"
        bybit.cancel_all_orders = AsyncMock(return_value=3)
        bybit.get_positions = AsyncMock(return_value=[])
        bybit.place_order = AsyncMock()

        return {"binance": binance, "bybit": bybit}

    @pytest.fixture
    def risk_manager(self, trading_config, mock_exchanges) -> RiskManager:
        """Create risk manager instance."""
        return RiskManager(trading_config, mock_exchanges)

    def test_init(self, risk_manager, trading_config, mock_exchanges):
        """Test risk manager initialization."""
        assert risk_manager.config == trading_config
        assert risk_manager.exchanges == mock_exchanges
        assert risk_manager._kill_switch_active is False
        assert risk_manager._paused_pairs == {}

    # ==================== Position Limits ====================

    def test_check_position_limit_within_limit(self, risk_manager):
        """Test position within limit."""
        result = risk_manager.check_position_limit("BTC/USDT:USDT", Decimal("40000"))
        assert result is True

    def test_check_position_limit_at_limit(self, risk_manager):
        """Test position at exactly the limit."""
        result = risk_manager.check_position_limit("BTC/USDT:USDT", Decimal("50000"))
        assert result is True

    def test_check_position_limit_exceeds_limit(self, risk_manager):
        """Test position exceeding limit."""
        result = risk_manager.check_position_limit("BTC/USDT:USDT", Decimal("60000"))
        assert result is False

    # ==================== Pair Pausing ====================

    def test_is_pair_paused_not_paused(self, risk_manager):
        """Test checking if non-paused pair is paused."""
        result = risk_manager.is_pair_paused("BTC/USDT:USDT")
        assert result is False

    def test_is_pair_paused_active(self, risk_manager):
        """Test checking if actively paused pair is paused."""
        future_time = datetime.now(timezone.utc) + timedelta(hours=1)
        risk_manager._paused_pairs["BTC/USDT:USDT"] = future_time

        result = risk_manager.is_pair_paused("BTC/USDT:USDT")
        assert result is True

    def test_is_pair_paused_expired(self, risk_manager):
        """Test that expired pause is cleared."""
        past_time = datetime.now(timezone.utc) - timedelta(hours=1)
        risk_manager._paused_pairs["BTC/USDT:USDT"] = past_time

        result = risk_manager.is_pair_paused("BTC/USDT:USDT")

        assert result is False
        assert "BTC/USDT:USDT" not in risk_manager._paused_pairs

    def test_pause_pair(self, risk_manager):
        """Test pausing a pair."""
        risk_manager.pause_pair("BTC/USDT:USDT", cooldown_hours=2.0)

        assert "BTC/USDT:USDT" in risk_manager._paused_pairs
        expiry = risk_manager._paused_pairs["BTC/USDT:USDT"]
        expected = datetime.now(timezone.utc) + timedelta(hours=2)
        # Allow 1 second tolerance
        assert abs((expiry - expected).total_seconds()) < 1

    def test_get_paused_pairs(self, risk_manager):
        """Test getting paused pairs."""
        future_time = datetime.now(timezone.utc) + timedelta(hours=1)
        risk_manager._paused_pairs["BTC/USDT:USDT"] = future_time
        risk_manager._paused_pairs["ETH/USDT:USDT"] = future_time

        result = risk_manager.get_paused_pairs()

        assert len(result) == 2
        assert "BTC/USDT:USDT" in result
        assert "ETH/USDT:USDT" in result
        # Should return a copy
        assert result is not risk_manager._paused_pairs

    # ==================== Kill Switch ====================

    def test_is_kill_switch_active_default(self, risk_manager):
        """Test kill switch is inactive by default."""
        assert risk_manager.is_kill_switch_active is False

    def test_is_trading_enabled_default(self, risk_manager):
        """Test trading is enabled by default."""
        assert risk_manager.is_trading_enabled is True

    @pytest.mark.asyncio
    async def test_activate_kill_switch(self, risk_manager, mock_exchanges):
        """Test activating kill switch."""
        await risk_manager.activate_kill_switch("Test activation")

        assert risk_manager._kill_switch_active is True
        assert risk_manager._kill_switch_activated_at is not None
        assert risk_manager.is_trading_enabled is False

        # Should cancel all orders on both exchanges
        mock_exchanges["binance"].cancel_all_orders.assert_called_once()
        mock_exchanges["bybit"].cancel_all_orders.assert_called_once()

    @pytest.mark.asyncio
    async def test_activate_kill_switch_already_active(self, risk_manager):
        """Test activating kill switch when already active."""
        risk_manager._kill_switch_active = True

        await risk_manager.activate_kill_switch("Test")

        # Should not double-activate (timestamp stays None since we set active manually)
        assert risk_manager._kill_switch_activated_at is None

    def test_deactivate_kill_switch(self, risk_manager):
        """Test deactivating kill switch."""
        risk_manager._kill_switch_active = True
        risk_manager._kill_switch_activated_at = datetime.now(timezone.utc)

        risk_manager.deactivate_kill_switch()

        assert risk_manager._kill_switch_active is False
        assert risk_manager._kill_switch_activated_at is None
        assert risk_manager.is_trading_enabled is True

    def test_deactivate_kill_switch_not_active(self, risk_manager):
        """Test deactivating kill switch when not active."""
        risk_manager.deactivate_kill_switch()

        # Should not raise, just do nothing
        assert risk_manager._kill_switch_active is False

    @pytest.mark.asyncio
    async def test_kill_switch_closes_positions(self, risk_manager, mock_exchanges):
        """Test that kill switch closes all positions."""
        # Setup mock positions
        mock_exchanges["binance"].get_positions.return_value = [
            ExchangePosition(
                exchange="binance",
                symbol="BTC/USDT:USDT",
                side=PositionSide.LONG,
                size=Decimal("0.1"),
                entry_price=Decimal("50000"),
                mark_price=Decimal("50100"),
                liquidation_price=Decimal("45000"),
                unrealized_pnl=Decimal("10"),
                leverage=5,
                margin_type="cross",
                timestamp=datetime.now(timezone.utc),
            ),
        ]

        await risk_manager.activate_kill_switch("Test")

        # Should have tried to close the position
        mock_exchanges["binance"].place_order.assert_called()

    @pytest.mark.asyncio
    async def test_kill_switch_handles_cancel_errors(self, risk_manager, mock_exchanges):
        """Test that kill switch handles cancel errors gracefully."""
        mock_exchanges["binance"].cancel_all_orders.side_effect = Exception("API error")

        # Should not raise
        await risk_manager.activate_kill_switch("Test")

        assert risk_manager._kill_switch_active is True

    # ==================== Risk Checks ====================

    def test_can_open_position_success(self, risk_manager):
        """Test successful position opening check."""
        can_open, reason = risk_manager.can_open_position(
            "BTC/USDT:USDT",
            Decimal("40000")
        )

        assert can_open is True
        assert reason == "OK"

    def test_can_open_position_kill_switch_active(self, risk_manager):
        """Test position opening blocked by kill switch."""
        risk_manager._kill_switch_active = True

        can_open, reason = risk_manager.can_open_position(
            "BTC/USDT:USDT",
            Decimal("40000")
        )

        assert can_open is False
        assert "Kill switch" in reason

    def test_can_open_position_pair_paused(self, risk_manager):
        """Test position opening blocked by paused pair."""
        future_time = datetime.now(timezone.utc) + timedelta(hours=1)
        risk_manager._paused_pairs["BTC/USDT:USDT"] = future_time

        can_open, reason = risk_manager.can_open_position(
            "BTC/USDT:USDT",
            Decimal("40000")
        )

        assert can_open is False
        assert "paused" in reason

    def test_can_open_position_exceeds_limit(self, risk_manager):
        """Test position opening blocked by size limit."""
        can_open, reason = risk_manager.can_open_position(
            "BTC/USDT:USDT",
            Decimal("60000")
        )

        assert can_open is False
        assert "exceeds limit" in reason

    # ==================== Alert Callback ====================

    def test_set_alert_callback(self, risk_manager):
        """Test setting alert callback."""
        callback = AsyncMock()

        risk_manager.set_alert_callback(callback)

        assert risk_manager._alert_callback == callback

    @pytest.mark.asyncio
    async def test_send_alert(self, risk_manager):
        """Test sending alert via callback."""
        callback = AsyncMock()
        risk_manager._alert_callback = callback

        await risk_manager._send_alert("WARNING", "Test Title", "Test message")

        callback.assert_called_once_with("WARNING", "Test Title", "Test message")

    @pytest.mark.asyncio
    async def test_send_alert_handles_errors(self, risk_manager):
        """Test that alert errors are handled gracefully."""
        callback = AsyncMock(side_effect=Exception("Callback error"))
        risk_manager._alert_callback = callback

        # Should not raise
        await risk_manager._send_alert("WARNING", "Test", "Test")

    @pytest.mark.asyncio
    async def test_send_alert_no_callback(self, risk_manager):
        """Test sending alert with no callback set."""
        # Should not raise
        await risk_manager._send_alert("WARNING", "Test", "Test")

    # ==================== Risk Status ====================

    def test_get_risk_status(self, risk_manager):
        """Test getting risk status."""
        future_time = datetime.now(timezone.utc) + timedelta(hours=1)
        risk_manager._paused_pairs["BTC/USDT:USDT"] = future_time

        status = risk_manager.get_risk_status()

        assert status["kill_switch_active"] is False
        assert status["kill_switch_activated_at"] is None
        assert status["trading_enabled"] is True
        assert "BTC/USDT:USDT" in status["paused_pairs"]
        assert status["max_position_per_pair"] == 50000.0

    def test_get_risk_status_kill_switch_active(self, risk_manager):
        """Test getting risk status with kill switch active."""
        now = datetime.now(timezone.utc)
        risk_manager._kill_switch_active = True
        risk_manager._kill_switch_activated_at = now

        status = risk_manager.get_risk_status()

        assert status["kill_switch_active"] is True
        assert status["kill_switch_activated_at"] == now.isoformat()
        assert status["trading_enabled"] is False

    # ==================== Liquidation Detection ====================

    @pytest.mark.asyncio
    async def test_check_for_liquidations_none(self, risk_manager, mock_exchanges):
        """Test checking for liquidations when none exist."""
        mock_exchanges["binance"].get_positions.return_value = []
        mock_exchanges["bybit"].get_positions.return_value = []

        liquidations = await risk_manager.check_for_liquidations()

        assert liquidations == []

    @pytest.mark.asyncio
    async def test_check_for_liquidations_handles_errors(self, risk_manager, mock_exchanges):
        """Test liquidation check handles errors gracefully."""
        mock_exchanges["binance"].get_positions.side_effect = Exception("API error")
        mock_exchanges["bybit"].get_positions.return_value = []

        # Should not raise
        liquidations = await risk_manager.check_for_liquidations()

        assert liquidations == []

    @pytest.mark.asyncio
    async def test_handle_liquidation(self, risk_manager, mock_exchanges):
        """Test handling a liquidation event."""
        await risk_manager.handle_liquidation(
            position_id="test-pos-001",
            liquidated_exchange="binance",
            surviving_exchange="bybit",
            surviving_symbol="BTC/USDT:USDT",
            surviving_side="LONG",
            surviving_size=Decimal("0.1"),
        )

        # Should close surviving leg
        mock_exchanges["bybit"].place_order.assert_called_once()

        # Should pause the pair
        assert risk_manager.is_pair_paused("BTC/USDT:USDT")

    @pytest.mark.asyncio
    async def test_handle_liquidation_close_short(self, risk_manager, mock_exchanges):
        """Test handling liquidation with short surviving side."""
        await risk_manager.handle_liquidation(
            position_id="test-pos-001",
            liquidated_exchange="binance",
            surviving_exchange="bybit",
            surviving_symbol="BTC/USDT:USDT",
            surviving_side="SHORT",
            surviving_size=Decimal("0.1"),
        )

        # Should close with BUY order (opposite of SHORT)
        call = mock_exchanges["bybit"].place_order.call_args
        order = call[0][0]
        assert order.side == OrderSide.BUY

    @pytest.mark.asyncio
    async def test_handle_liquidation_close_error(self, risk_manager, mock_exchanges):
        """Test handling liquidation when close fails."""
        mock_exchanges["bybit"].place_order.side_effect = Exception("Order failed")

        # Should not raise
        await risk_manager.handle_liquidation(
            position_id="test-pos-001",
            liquidated_exchange="binance",
            surviving_exchange="bybit",
            surviving_symbol="BTC/USDT:USDT",
            surviving_side="LONG",
            surviving_size=Decimal("0.1"),
        )

        # Should still pause the pair
        assert risk_manager.is_pair_paused("BTC/USDT:USDT")
