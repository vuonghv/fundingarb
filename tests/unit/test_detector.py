"""
Unit tests for arbitrage detector module.
"""

from datetime import datetime, timezone, timedelta
from decimal import Decimal

import pytest

from backend.config.schema import TradingConfig
from backend.engine.detector import ArbitrageDetector, ArbitrageOpportunity
from backend.exchanges.types import FundingRate


class TestArbitrageDetector:
    """Tests for ArbitrageDetector."""

    @pytest.fixture
    def trading_config(self) -> TradingConfig:
        """Create trading config for tests."""
        return TradingConfig(
            symbols=["BTC/USDT:USDT", "ETH/USDT:USDT"],
            min_spread_base=Decimal("0.0001"),
            min_spread_per_10k=Decimal("0.00001"),
            entry_buffer_minutes=20,
            max_position_per_pair_usd=Decimal("50000"),
            simulation_mode=True,
        )

    @pytest.fixture
    def detector(self, trading_config) -> ArbitrageDetector:
        """Create detector instance."""
        return ArbitrageDetector(trading_config)

    def test_calculate_threshold_small_size(self, detector):
        """Test threshold calculation for small position size."""
        threshold = detector.calculate_threshold(Decimal("10000"))
        assert float(threshold) == pytest.approx(0.00011)

    def test_calculate_threshold_large_size(self, detector):
        """Test threshold calculation for large position size."""
        threshold = detector.calculate_threshold(Decimal("50000"))
        assert float(threshold) == pytest.approx(0.00015)

    def test_find_opportunities_valid_spread(self, detector):
        """Test finding opportunity with valid spread."""
        now = datetime.now(timezone.utc)
        next_funding = now + timedelta(minutes=15)

        # Spread needs to be > 0.16% (0.0016) to cover fees
        # Fees = position * 0.04% * 2 trades * 2 legs = 0.16%
        rates = {
            "binance": {
                "BTC/USDT:USDT": FundingRate(
                    exchange="binance",
                    symbol="BTC/USDT:USDT",
                    rate=Decimal("0.0020"),  # Higher rate - will be short
                    predicted_rate=None,
                    next_funding_time=next_funding,
                    timestamp=now,
                ),
            },
            "bybit": {
                "BTC/USDT:USDT": FundingRate(
                    exchange="bybit",
                    symbol="BTC/USDT:USDT",
                    rate=Decimal("-0.0005"),  # Lower rate - will be long
                    predicted_rate=None,
                    next_funding_time=next_funding,
                    timestamp=now,
                ),
            },
        }

        opportunities = detector.find_opportunities(rates, Decimal("10000"))

        assert len(opportunities) == 1
        opp = opportunities[0]
        assert opp.symbol == "BTC/USDT:USDT"
        assert opp.long_exchange == "bybit"
        assert opp.short_exchange == "binance"
        assert float(opp.spread) == pytest.approx(0.0025)  # 0.0020 - (-0.0005)

    def test_find_opportunities_insufficient_spread(self, detector):
        """Test that insufficient spread is not detected."""
        now = datetime.now(timezone.utc)
        next_funding = now + timedelta(minutes=15)

        rates = {
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
            "bybit": {
                "BTC/USDT:USDT": FundingRate(
                    exchange="bybit",
                    symbol="BTC/USDT:USDT",
                    rate=Decimal("0.00009"),  # Very small difference
                    predicted_rate=None,
                    next_funding_time=next_funding,
                    timestamp=now,
                ),
            },
        }

        opportunities = detector.find_opportunities(rates, Decimal("10000"))
        assert len(opportunities) == 0

    def test_find_opportunities_single_exchange(self, detector):
        """Test with only one exchange (no arbitrage possible)."""
        now = datetime.now(timezone.utc)
        next_funding = now + timedelta(minutes=15)

        rates = {
            "binance": {
                "BTC/USDT:USDT": FundingRate(
                    exchange="binance",
                    symbol="BTC/USDT:USDT",
                    rate=Decimal("0.0003"),
                    predicted_rate=None,
                    next_funding_time=next_funding,
                    timestamp=now,
                ),
            },
        }

        opportunities = detector.find_opportunities(rates, Decimal("10000"))
        assert len(opportunities) == 0

    def test_find_opportunities_sorted_by_spread(self, detector):
        """Test that opportunities are sorted by spread (highest first)."""
        now = datetime.now(timezone.utc)
        next_funding = now + timedelta(minutes=15)

        # Spreads need to be > 0.16% to cover fees
        rates = {
            "binance": {
                "BTC/USDT:USDT": FundingRate(
                    exchange="binance",
                    symbol="BTC/USDT:USDT",
                    rate=Decimal("0.0020"),  # BTC spread = 0.0025
                    predicted_rate=None,
                    next_funding_time=next_funding,
                    timestamp=now,
                ),
                "ETH/USDT:USDT": FundingRate(
                    exchange="binance",
                    symbol="ETH/USDT:USDT",
                    rate=Decimal("0.0030"),  # ETH spread = 0.0035 (higher)
                    predicted_rate=None,
                    next_funding_time=next_funding,
                    timestamp=now,
                ),
            },
            "bybit": {
                "BTC/USDT:USDT": FundingRate(
                    exchange="bybit",
                    symbol="BTC/USDT:USDT",
                    rate=Decimal("-0.0005"),
                    predicted_rate=None,
                    next_funding_time=next_funding,
                    timestamp=now,
                ),
                "ETH/USDT:USDT": FundingRate(
                    exchange="bybit",
                    symbol="ETH/USDT:USDT",
                    rate=Decimal("-0.0005"),
                    predicted_rate=None,
                    next_funding_time=next_funding,
                    timestamp=now,
                ),
            },
        }

        opportunities = detector.find_opportunities(rates, Decimal("10000"))

        assert len(opportunities) == 2
        # Verify sorted by spread descending (ETH should be first with higher spread)
        assert opportunities[0].symbol == "ETH/USDT:USDT"
        assert opportunities[1].symbol == "BTC/USDT:USDT"
        assert opportunities[0].spread > opportunities[1].spread

    def test_find_best_opportunity(self, detector):
        """Test finding the single best opportunity."""
        now = datetime.now(timezone.utc)
        next_funding = now + timedelta(minutes=15)

        # Spread needs to be > 0.16% to cover fees
        rates = {
            "binance": {
                "BTC/USDT:USDT": FundingRate(
                    exchange="binance",
                    symbol="BTC/USDT:USDT",
                    rate=Decimal("0.0020"),
                    predicted_rate=None,
                    next_funding_time=next_funding,
                    timestamp=now,
                ),
            },
            "bybit": {
                "BTC/USDT:USDT": FundingRate(
                    exchange="bybit",
                    symbol="BTC/USDT:USDT",
                    rate=Decimal("-0.0005"),
                    predicted_rate=None,
                    next_funding_time=next_funding,
                    timestamp=now,
                ),
            },
        }

        best = detector.find_best_opportunity(rates, Decimal("10000"))
        assert best is not None
        assert best.symbol == "BTC/USDT:USDT"

    def test_find_best_opportunity_with_exclusions(self, detector):
        """Test finding best opportunity excluding certain pairs."""
        now = datetime.now(timezone.utc)
        next_funding = now + timedelta(minutes=15)

        # Spread needs to be > 0.16% to cover fees
        rates = {
            "binance": {
                "BTC/USDT:USDT": FundingRate(
                    exchange="binance",
                    symbol="BTC/USDT:USDT",
                    rate=Decimal("0.0020"),
                    predicted_rate=None,
                    next_funding_time=next_funding,
                    timestamp=now,
                ),
            },
            "bybit": {
                "BTC/USDT:USDT": FundingRate(
                    exchange="bybit",
                    symbol="BTC/USDT:USDT",
                    rate=Decimal("-0.0005"),
                    predicted_rate=None,
                    next_funding_time=next_funding,
                    timestamp=now,
                ),
            },
        }

        # Exclude BTC
        best = detector.find_best_opportunity(
            rates,
            Decimal("10000"),
            excluded_pairs=["BTC/USDT:USDT"]
        )
        assert best is None

    def test_calculate_fees(self, detector):
        """Test fee calculation."""
        fees = detector.calculate_fees(
            Decimal("10000"),
            "binance",
            "bybit"
        )
        # Default fee 0.04% * 2 trades * 2 legs = 0.16%
        assert float(fees) == pytest.approx(16.0)


class TestArbitrageOpportunity:
    """Tests for ArbitrageOpportunity dataclass."""

    def test_opportunity_creation(self):
        """Test creating an opportunity."""
        opp = ArbitrageOpportunity(
            symbol="BTC/USDT:USDT",
            long_exchange="bybit",
            short_exchange="binance",
            long_rate=Decimal("-0.0001"),
            short_rate=Decimal("0.0003"),
            spread=Decimal("0.0004"),
            expected_profit_per_funding=Decimal("4.00"),
            expected_daily_profit=Decimal("12.00"),
            annualized_apr=Decimal("43.8"),
            next_funding_time=datetime.now(timezone.utc) + timedelta(minutes=30),
            seconds_to_funding=1800.0,
            detected_at=datetime.now(timezone.utc),
        )

        assert opp.symbol == "BTC/USDT:USDT"
        assert opp.long_exchange == "bybit"
        assert opp.short_exchange == "binance"
        assert float(opp.spread) == 0.0004

    def test_opportunity_spread_percent(self):
        """Test spread percentage property."""
        opp = ArbitrageOpportunity(
            symbol="BTC/USDT:USDT",
            long_exchange="bybit",
            short_exchange="binance",
            long_rate=Decimal("-0.0001"),
            short_rate=Decimal("0.0003"),
            spread=Decimal("0.0004"),
            expected_profit_per_funding=Decimal("4.00"),
            expected_daily_profit=Decimal("12.00"),
            annualized_apr=Decimal("43.8"),
            next_funding_time=datetime.now(timezone.utc) + timedelta(minutes=30),
            seconds_to_funding=1800.0,
            detected_at=datetime.now(timezone.utc),
        )

        assert float(opp.spread_percent) == pytest.approx(0.04)

    def test_opportunity_is_urgent(self):
        """Test urgent detection (< 5 minutes)."""
        # Not urgent
        opp1 = ArbitrageOpportunity(
            symbol="BTC/USDT:USDT",
            long_exchange="bybit",
            short_exchange="binance",
            long_rate=Decimal("-0.0001"),
            short_rate=Decimal("0.0003"),
            spread=Decimal("0.0004"),
            expected_profit_per_funding=Decimal("4.00"),
            expected_daily_profit=Decimal("12.00"),
            annualized_apr=Decimal("43.8"),
            next_funding_time=datetime.now(timezone.utc) + timedelta(minutes=30),
            seconds_to_funding=1800.0,
            detected_at=datetime.now(timezone.utc),
        )
        assert opp1.is_urgent is False

        # Urgent
        opp2 = ArbitrageOpportunity(
            symbol="BTC/USDT:USDT",
            long_exchange="bybit",
            short_exchange="binance",
            long_rate=Decimal("-0.0001"),
            short_rate=Decimal("0.0003"),
            spread=Decimal("0.0004"),
            expected_profit_per_funding=Decimal("4.00"),
            expected_daily_profit=Decimal("12.00"),
            annualized_apr=Decimal("43.8"),
            next_funding_time=datetime.now(timezone.utc) + timedelta(minutes=2),
            seconds_to_funding=120.0,
            detected_at=datetime.now(timezone.utc),
        )
        assert opp2.is_urgent is True
