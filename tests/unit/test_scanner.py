"""
Unit tests for funding rate scanner module.
"""

from datetime import datetime, timezone, timedelta
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.engine.scanner import FundingRateScanner
from backend.exchanges.types import FundingRate


class TestFundingRateScanner:
    """Tests for FundingRateScanner."""

    @pytest.fixture
    def mock_exchanges(self):
        """Create mock exchange adapters."""
        binance = MagicMock()
        binance.name = "binance"
        binance.subscribe_funding_rates = AsyncMock()
        binance.get_funding_rates = AsyncMock(return_value={
            "BTC/USDT:USDT": FundingRate(
                exchange="binance",
                symbol="BTC/USDT:USDT",
                rate=Decimal("0.0001"),
                predicted_rate=Decimal("0.00008"),
                next_funding_time=datetime.now(timezone.utc) + timedelta(hours=4),
                timestamp=datetime.now(timezone.utc),
            ),
        })

        bybit = MagicMock()
        bybit.name = "bybit"
        bybit.subscribe_funding_rates = AsyncMock()
        bybit.get_funding_rates = AsyncMock(return_value={
            "BTC/USDT:USDT": FundingRate(
                exchange="bybit",
                symbol="BTC/USDT:USDT",
                rate=Decimal("-0.0002"),
                predicted_rate=Decimal("-0.00015"),
                next_funding_time=datetime.now(timezone.utc) + timedelta(hours=4),
                timestamp=datetime.now(timezone.utc),
            ),
        })

        return {"binance": binance, "bybit": bybit}

    @pytest.fixture
    def scanner(self, mock_exchanges):
        """Create scanner instance."""
        return FundingRateScanner(mock_exchanges)

    def test_init(self, scanner, mock_exchanges):
        """Test scanner initialization."""
        assert scanner.exchanges == mock_exchanges
        assert scanner._running is False
        assert scanner._rates == {}
        assert scanner._callbacks == []

    @pytest.mark.asyncio
    async def test_start(self, scanner, mock_exchanges):
        """Test starting the scanner."""
        symbols = ["BTC/USDT:USDT", "ETH/USDT:USDT"]

        await scanner.start(symbols)

        assert scanner._running is True
        assert scanner._symbols == set(symbols)

        # Should subscribe on each exchange
        mock_exchanges["binance"].subscribe_funding_rates.assert_called_once()
        mock_exchanges["bybit"].subscribe_funding_rates.assert_called_once()

        # Should fetch initial rates
        mock_exchanges["binance"].get_funding_rates.assert_called_once()
        mock_exchanges["bybit"].get_funding_rates.assert_called_once()

    @pytest.mark.asyncio
    async def test_start_already_running(self, scanner):
        """Test starting when already running logs warning."""
        scanner._running = True

        await scanner.start(["BTC/USDT:USDT"])

        # Should not change state
        assert scanner._running is True

    @pytest.mark.asyncio
    async def test_stop(self, scanner):
        """Test stopping the scanner."""
        scanner._running = True

        await scanner.stop()

        assert scanner._running is False

    def test_on_rate_update(self, scanner):
        """Test handling rate updates."""
        now = datetime.now(timezone.utc)
        rate = FundingRate(
            exchange="binance",
            symbol="BTC/USDT:USDT",
            rate=Decimal("0.0001"),
            predicted_rate=None,
            next_funding_time=now + timedelta(hours=4),
            timestamp=now,
        )

        scanner._on_rate_update("binance", rate)

        assert "binance" in scanner._rates
        assert "BTC/USDT:USDT" in scanner._rates["binance"]
        assert scanner._rates["binance"]["BTC/USDT:USDT"] == rate
        assert "binance" in scanner._last_update

    def test_on_rate_update_notifies_callbacks(self, scanner):
        """Test that rate updates trigger callbacks."""
        callback = MagicMock()
        scanner._callbacks.append(callback)

        now = datetime.now(timezone.utc)
        rate = FundingRate(
            exchange="binance",
            symbol="BTC/USDT:USDT",
            rate=Decimal("0.0001"),
            predicted_rate=None,
            next_funding_time=now + timedelta(hours=4),
            timestamp=now,
        )

        scanner._on_rate_update("binance", rate)

        callback.assert_called_once()

    def test_on_update_registers_callback(self, scanner):
        """Test registering update callbacks."""
        callback = MagicMock()

        scanner.on_update(callback)

        assert callback in scanner._callbacks

    def test_get_rates(self, scanner):
        """Test getting all rates."""
        now = datetime.now(timezone.utc)
        rate = FundingRate(
            exchange="binance",
            symbol="BTC/USDT:USDT",
            rate=Decimal("0.0001"),
            predicted_rate=None,
            next_funding_time=now + timedelta(hours=4),
            timestamp=now,
        )
        scanner._rates = {"binance": {"BTC/USDT:USDT": rate}}

        rates = scanner.get_rates()

        assert rates == {"binance": {"BTC/USDT:USDT": rate}}
        # Should return a copy
        assert rates is not scanner._rates

    def test_get_rates_for_symbol(self, scanner):
        """Test getting rates for a specific symbol."""
        now = datetime.now(timezone.utc)
        btc_binance = FundingRate(
            exchange="binance",
            symbol="BTC/USDT:USDT",
            rate=Decimal("0.0001"),
            predicted_rate=None,
            next_funding_time=now + timedelta(hours=4),
            timestamp=now,
        )
        btc_bybit = FundingRate(
            exchange="bybit",
            symbol="BTC/USDT:USDT",
            rate=Decimal("-0.0002"),
            predicted_rate=None,
            next_funding_time=now + timedelta(hours=4),
            timestamp=now,
        )
        scanner._rates = {
            "binance": {"BTC/USDT:USDT": btc_binance},
            "bybit": {"BTC/USDT:USDT": btc_bybit},
        }

        rates = scanner.get_rates_for_symbol("BTC/USDT:USDT")

        assert rates == {"binance": btc_binance, "bybit": btc_bybit}

    def test_get_rate(self, scanner):
        """Test getting a specific rate."""
        now = datetime.now(timezone.utc)
        rate = FundingRate(
            exchange="binance",
            symbol="BTC/USDT:USDT",
            rate=Decimal("0.0001"),
            predicted_rate=None,
            next_funding_time=now + timedelta(hours=4),
            timestamp=now,
        )
        scanner._rates = {"binance": {"BTC/USDT:USDT": rate}}

        result = scanner.get_rate("binance", "BTC/USDT:USDT")

        assert result == rate

    def test_get_rate_not_found(self, scanner):
        """Test getting a rate that doesn't exist."""
        result = scanner.get_rate("binance", "BTC/USDT:USDT")

        assert result is None

    def test_get_next_funding_time(self, scanner):
        """Test getting next funding time across exchanges."""
        now = datetime.now(timezone.utc)
        earlier = now + timedelta(hours=2)
        later = now + timedelta(hours=4)

        scanner._rates = {
            "binance": {
                "BTC/USDT:USDT": FundingRate(
                    exchange="binance",
                    symbol="BTC/USDT:USDT",
                    rate=Decimal("0.0001"),
                    predicted_rate=None,
                    next_funding_time=later,
                    timestamp=now,
                ),
            },
            "bybit": {
                "BTC/USDT:USDT": FundingRate(
                    exchange="bybit",
                    symbol="BTC/USDT:USDT",
                    rate=Decimal("-0.0002"),
                    predicted_rate=None,
                    next_funding_time=earlier,
                    timestamp=now,
                ),
            },
        }

        result = scanner.get_next_funding_time("BTC/USDT:USDT")

        # Should return the earliest time
        assert result == earlier

    def test_get_next_funding_time_not_found(self, scanner):
        """Test getting next funding time when symbol not found."""
        result = scanner.get_next_funding_time("UNKNOWN/USDT:USDT")

        assert result is None

    def test_get_time_to_funding(self, scanner):
        """Test getting time to next funding."""
        now = datetime.now(timezone.utc)
        next_funding = now + timedelta(hours=2)

        scanner._rates = {
            "binance": {
                "BTC/USDT:USDT": FundingRate(
                    exchange="binance",
                    symbol="BTC/USDT:USDT",
                    rate=Decimal("0.0001"),
                    predicted_rate=None,
                    next_funding_time=next_funding,
                    timestamp=now,
                ),
            },
        }

        result = scanner.get_time_to_funding("BTC/USDT:USDT")

        # Should be approximately 2 hours in seconds
        assert result is not None
        assert 7100 < result < 7300  # Allow some tolerance

    def test_is_running_property(self, scanner):
        """Test is_running property."""
        assert scanner.is_running is False

        scanner._running = True
        assert scanner.is_running is True

    def test_monitored_symbols_property(self, scanner):
        """Test monitored_symbols property."""
        scanner._symbols = {"BTC/USDT:USDT", "ETH/USDT:USDT"}

        result = scanner.monitored_symbols

        assert result == {"BTC/USDT:USDT", "ETH/USDT:USDT"}
        # Should return a copy
        assert result is not scanner._symbols

    def test_get_exchange_status_with_updates(self, scanner):
        """Test getting exchange status with recent updates."""
        now = datetime.now(timezone.utc)
        scanner._last_update = {
            "binance": now - timedelta(seconds=30),
            "bybit": now - timedelta(seconds=60),
        }

        status = scanner.get_exchange_status()

        assert "binance" in status
        assert status["binance"]["connected"] is True
        assert status["binance"]["stale"] is False
        assert 25 < status["binance"]["seconds_ago"] < 35

        assert "bybit" in status
        assert status["bybit"]["connected"] is True
        assert status["bybit"]["stale"] is False

    def test_get_exchange_status_stale(self, scanner):
        """Test getting exchange status with stale data."""
        now = datetime.now(timezone.utc)
        scanner._last_update = {
            "binance": now - timedelta(minutes=5),  # Stale (> 2 minutes)
        }

        status = scanner.get_exchange_status()

        assert status["binance"]["stale"] is True

    def test_get_exchange_status_no_updates(self, scanner, mock_exchanges):
        """Test getting exchange status without any updates."""
        status = scanner.get_exchange_status()

        assert status["binance"]["connected"] is False
        assert status["binance"]["last_update"] is None
        assert status["binance"]["stale"] is True

    def test_callback_error_handling(self, scanner):
        """Test that callback errors are handled gracefully."""
        def bad_callback(rates):
            raise ValueError("Test error")

        scanner._callbacks.append(bad_callback)

        now = datetime.now(timezone.utc)
        rate = FundingRate(
            exchange="binance",
            symbol="BTC/USDT:USDT",
            rate=Decimal("0.0001"),
            predicted_rate=None,
            next_funding_time=now + timedelta(hours=4),
            timestamp=now,
        )

        # Should not raise
        scanner._on_rate_update("binance", rate)

        # Rate should still be updated
        assert "binance" in scanner._rates

    @pytest.mark.asyncio
    async def test_subscription_error_handling(self, scanner, mock_exchanges):
        """Test that subscription errors are handled gracefully."""
        mock_exchanges["binance"].subscribe_funding_rates.side_effect = Exception("Connection failed")

        # Should not raise
        await scanner.start(["BTC/USDT:USDT"])

        # Scanner should still be running
        assert scanner._running is True

    @pytest.mark.asyncio
    async def test_fetch_rates_error_handling(self, scanner, mock_exchanges):
        """Test that fetch errors are handled gracefully."""
        mock_exchanges["binance"].get_funding_rates.side_effect = Exception("API error")

        # Should not raise
        await scanner.start(["BTC/USDT:USDT"])

        # Scanner should still be running
        assert scanner._running is True
