"""
Unit tests for funding rate scanner module.
"""

from datetime import datetime, timezone, timedelta
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.engine.scanner import FundingRateScanner
from backend.exchanges.types import FundingRate


class TestFundingRateSchema:
    """Tests to ensure FundingRate schema has required properties."""

    def test_funding_rate_has_daily_rate_property(self):
        """Verify FundingRate has daily_rate property."""
        rate = FundingRate(
            exchange="binance",
            symbol="BTC/USDT:USDT",
            rate=Decimal("0.0001"),  # 0.01% per 8h
            predicted_rate=None,
            next_funding_time=datetime.now(timezone.utc) + timedelta(hours=4),
            timestamp=datetime.now(timezone.utc),
            interval_hours=8,
        )

        # Should have daily_rate property
        assert hasattr(rate, "daily_rate"), (
            "FundingRate must have 'daily_rate' property"
        )
        # 0.0001 * 3 (periods per day) = 0.0003
        assert float(rate.daily_rate) == pytest.approx(0.0003)

    def test_funding_rate_has_periods_per_day_property(self):
        """Verify FundingRate has periods_per_day property."""
        rate_8h = FundingRate(
            exchange="binance",
            symbol="BTC/USDT:USDT",
            rate=Decimal("0.0001"),
            predicted_rate=None,
            next_funding_time=datetime.now(timezone.utc),
            timestamp=datetime.now(timezone.utc),
            interval_hours=8,
        )

        rate_1h = FundingRate(
            exchange="dydx",
            symbol="BTC/USD",
            rate=Decimal("0.0001"),
            predicted_rate=None,
            next_funding_time=datetime.now(timezone.utc),
            timestamp=datetime.now(timezone.utc),
            interval_hours=1,
        )

        assert hasattr(rate_8h, "periods_per_day"), (
            "FundingRate must have 'periods_per_day' property"
        )
        assert float(rate_8h.periods_per_day) == pytest.approx(3.0)
        assert float(rate_1h.periods_per_day) == pytest.approx(24.0)

    def test_funding_rate_daily_rate_normalization(self):
        """Verify daily_rate correctly normalizes across different intervals."""
        # Same raw rate, different intervals
        rate_8h = FundingRate(
            exchange="binance",
            symbol="BTC/USDT:USDT",
            rate=Decimal("0.0001"),  # 0.01% per 8h
            predicted_rate=None,
            next_funding_time=datetime.now(timezone.utc),
            timestamp=datetime.now(timezone.utc),
            interval_hours=8,
        )

        rate_1h = FundingRate(
            exchange="dydx",
            symbol="BTC/USD",
            rate=Decimal("0.0001"),  # 0.01% per 1h
            predicted_rate=None,
            next_funding_time=datetime.now(timezone.utc),
            timestamp=datetime.now(timezone.utc),
            interval_hours=1,
        )

        # 1h rate should be 8x more valuable when normalized to daily
        # 8h: 0.0001 * 3 = 0.0003 daily
        # 1h: 0.0001 * 24 = 0.0024 daily
        assert float(rate_8h.daily_rate) == pytest.approx(0.0003)
        assert float(rate_1h.daily_rate) == pytest.approx(0.0024)
        assert float(rate_1h.daily_rate) == pytest.approx(float(rate_8h.daily_rate) * 8)

    def test_funding_rate_has_interval_hours_field(self):
        """Verify FundingRate has interval_hours field."""
        from dataclasses import fields
        field_names = {f.name for f in fields(FundingRate)}

        assert "interval_hours" in field_names, (
            "FundingRate must have 'interval_hours' field"
        )


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
        assert scanner._on_rates_callback is None
        assert scanner._poll_task is None

    @pytest.mark.asyncio
    async def test_start(self, scanner, mock_exchanges):
        """Test starting the scanner."""
        symbols = ["BTC/USDT:USDT", "ETH/USDT:USDT"]

        await scanner.start(symbols)

        assert scanner._running is True
        assert scanner._symbols == set(symbols)

        # Should fetch initial rates from all exchanges in parallel
        mock_exchanges["binance"].get_funding_rates.assert_called_once()
        mock_exchanges["bybit"].get_funding_rates.assert_called_once()

        # Should start polling task
        assert scanner._poll_task is not None

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

    @pytest.mark.asyncio
    async def test_start_with_async_callback(self, scanner, mock_exchanges):
        """Test starting scanner with async callback."""
        callback = AsyncMock()
        symbols = ["BTC/USDT:USDT"]

        await scanner.start(symbols, on_rates_update=callback)

        assert scanner._running is True
        assert scanner._on_rates_callback is callback

        # Callback should be called with initial rates
        callback.assert_called_once()

    @pytest.mark.asyncio
    async def test_fetch_all_rates_populates_cache(self, scanner, mock_exchanges):
        """Test that fetching rates populates the cache."""
        symbols = ["BTC/USDT:USDT"]

        await scanner.start(symbols)

        # Rates should be populated
        assert "binance" in scanner._rates
        assert "BTC/USDT:USDT" in scanner._rates["binance"]
        assert "bybit" in scanner._rates
        assert "BTC/USDT:USDT" in scanner._rates["bybit"]

        # Last update should be recorded
        assert "binance" in scanner._last_update
        assert "bybit" in scanner._last_update

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

    @pytest.mark.asyncio
    async def test_callback_error_handling(self, scanner, mock_exchanges):
        """Test that callback errors are handled gracefully."""
        async def bad_callback(rates):
            raise ValueError("Test error")

        symbols = ["BTC/USDT:USDT"]

        # Start with bad callback - should not raise
        await scanner.start(symbols, on_rates_update=bad_callback)

        # Scanner should still be running
        assert scanner._running is True
        # Rates should still be populated
        assert "binance" in scanner._rates

    @pytest.mark.asyncio
    async def test_parallel_fetch_handles_exchange_errors(self, scanner, mock_exchanges):
        """Test that errors from one exchange don't block others."""
        mock_exchanges["binance"].get_funding_rates.side_effect = Exception("API error")

        # Should not raise
        await scanner.start(["BTC/USDT:USDT"])

        # Scanner should still be running
        assert scanner._running is True
        # Bybit rates should still be populated
        assert "bybit" in scanner._rates
        # Binance should not have rates due to error
        assert "binance" not in scanner._rates or "BTC/USDT:USDT" not in scanner._rates.get("binance", {})

    @pytest.mark.asyncio
    async def test_fetch_rates_error_handling(self, scanner, mock_exchanges):
        """Test that fetch errors are handled gracefully."""
        mock_exchanges["binance"].get_funding_rates.side_effect = Exception("API error")

        # Should not raise
        await scanner.start(["BTC/USDT:USDT"])

        # Scanner should still be running
        assert scanner._running is True
