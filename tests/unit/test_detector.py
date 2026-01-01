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
            min_daily_spread_base=Decimal("0.0003"),  # 0.03% daily
            min_daily_spread_per_10k=Decimal("0.00003"),  # 0.003% daily per $10k
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
        # 0.0003 + (0.00003 * 1) = 0.00033
        threshold = detector.calculate_threshold(Decimal("10000"))
        assert float(threshold) == pytest.approx(0.00033)

    def test_calculate_threshold_large_size(self, detector):
        """Test threshold calculation for large position size."""
        # 0.0003 + (0.00003 * 5) = 0.00045
        threshold = detector.calculate_threshold(Decimal("50000"))
        assert float(threshold) == pytest.approx(0.00045)

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
        assert float(opp.spread) == pytest.approx(0.0025)  # 0.0020 - (-0.0005) raw
        # Daily spread = 0.0025 * 3 (both 8h intervals) = 0.0075
        assert float(opp.daily_spread) == pytest.approx(0.0075)
        assert opp.long_interval_hours == 8
        assert opp.short_interval_hours == 8

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
        # Verify sorted by daily_spread descending (ETH should be first with higher spread)
        assert opportunities[0].symbol == "ETH/USDT:USDT"
        assert opportunities[1].symbol == "BTC/USDT:USDT"
        assert opportunities[0].daily_spread > opportunities[1].daily_spread

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


class TestArbitrageOpportunitySchema:
    """Tests to ensure ArbitrageOpportunity schema is correct."""

    # Define expected fields - update this when schema changes
    EXPECTED_OPPORTUNITY_FIELDS = {
        "symbol",
        "long_exchange",
        "short_exchange",
        "long_interval_hours",
        "short_interval_hours",
        "long_rate",
        "short_rate",
        "long_daily_rate",
        "short_daily_rate",
        "daily_spread",
        "spread",
        "expected_daily_profit",
        "annualized_apr",
        "next_funding_time",
        "seconds_to_funding",
        "detected_at",
    }

    def test_opportunity_has_expected_fields(self):
        """Verify ArbitrageOpportunity has all expected field names."""
        from dataclasses import fields
        actual_fields = {f.name for f in fields(ArbitrageOpportunity)}

        assert self.EXPECTED_OPPORTUNITY_FIELDS == actual_fields, (
            f"ArbitrageOpportunity fields mismatch.\n"
            f"Missing: {self.EXPECTED_OPPORTUNITY_FIELDS - actual_fields}\n"
            f"Extra: {actual_fields - self.EXPECTED_OPPORTUNITY_FIELDS}"
        )

    def test_opportunity_has_interval_fields(self):
        """Ensure ArbitrageOpportunity has interval fields for both exchanges."""
        from dataclasses import fields
        field_names = {f.name for f in fields(ArbitrageOpportunity)}

        # Must have interval fields
        assert "long_interval_hours" in field_names, (
            "ArbitrageOpportunity must have 'long_interval_hours'"
        )
        assert "short_interval_hours" in field_names, (
            "ArbitrageOpportunity must have 'short_interval_hours'"
        )

        # Must have daily rate fields
        assert "long_daily_rate" in field_names, (
            "ArbitrageOpportunity must have 'long_daily_rate'"
        )
        assert "short_daily_rate" in field_names, (
            "ArbitrageOpportunity must have 'short_daily_rate'"
        )
        assert "daily_spread" in field_names, (
            "ArbitrageOpportunity must have 'daily_spread'"
        )

    def test_opportunity_must_not_have_old_fields(self):
        """Ensure old/removed fields are not present."""
        from dataclasses import fields
        field_names = {f.name for f in fields(ArbitrageOpportunity)}

        # These old fields should NOT exist
        assert "expected_profit_per_funding" not in field_names, (
            "ArbitrageOpportunity should NOT have 'expected_profit_per_funding' (removed)"
        )
        assert "funding_interval_hours" not in field_names, (
            "ArbitrageOpportunity should NOT have 'funding_interval_hours' "
            "(replaced by long_interval_hours/short_interval_hours)"
        )


class TestArbitrageOpportunity:
    """Tests for ArbitrageOpportunity dataclass."""

    def test_opportunity_creation(self):
        """Test creating an opportunity with daily normalized rates."""
        # Raw rates: long=-0.0001 (8h), short=0.0003 (8h)
        # Daily rates: long=-0.0003, short=0.0009
        # Daily spread: 0.0012
        opp = ArbitrageOpportunity(
            symbol="BTC/USDT:USDT",
            long_exchange="bybit",
            short_exchange="binance",
            long_interval_hours=8,
            short_interval_hours=8,
            long_rate=Decimal("-0.0001"),
            short_rate=Decimal("0.0003"),
            long_daily_rate=Decimal("-0.0003"),  # -0.0001 * 3
            short_daily_rate=Decimal("0.0009"),  # 0.0003 * 3
            daily_spread=Decimal("0.0012"),  # 0.0009 - (-0.0003)
            spread=Decimal("0.0004"),  # Raw spread for backwards compat
            expected_daily_profit=Decimal("12.00"),
            annualized_apr=Decimal("43.8"),
            next_funding_time=datetime.now(timezone.utc) + timedelta(minutes=30),
            seconds_to_funding=1800.0,
            detected_at=datetime.now(timezone.utc),
        )

        assert opp.symbol == "BTC/USDT:USDT"
        assert opp.long_exchange == "bybit"
        assert opp.short_exchange == "binance"
        assert opp.long_interval_hours == 8
        assert opp.short_interval_hours == 8
        assert float(opp.daily_spread) == pytest.approx(0.0012)

    def test_opportunity_spread_percent(self):
        """Test spread percentage property (daily normalized)."""
        opp = ArbitrageOpportunity(
            symbol="BTC/USDT:USDT",
            long_exchange="bybit",
            short_exchange="binance",
            long_interval_hours=8,
            short_interval_hours=8,
            long_rate=Decimal("-0.0001"),
            short_rate=Decimal("0.0003"),
            long_daily_rate=Decimal("-0.0003"),
            short_daily_rate=Decimal("0.0009"),
            daily_spread=Decimal("0.0012"),  # Daily spread
            spread=Decimal("0.0004"),  # Raw spread
            expected_daily_profit=Decimal("12.00"),
            annualized_apr=Decimal("43.8"),
            next_funding_time=datetime.now(timezone.utc) + timedelta(minutes=30),
            seconds_to_funding=1800.0,
            detected_at=datetime.now(timezone.utc),
        )

        # spread_percent now returns daily_spread as percentage
        assert float(opp.spread_percent) == pytest.approx(0.12)  # 0.0012 * 100

    def test_opportunity_is_urgent(self):
        """Test urgent detection (< 5 minutes)."""
        # Not urgent
        opp1 = ArbitrageOpportunity(
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
            spread=Decimal("0.0004"),
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
            long_interval_hours=8,
            short_interval_hours=8,
            long_rate=Decimal("-0.0001"),
            short_rate=Decimal("0.0003"),
            long_daily_rate=Decimal("-0.0003"),
            short_daily_rate=Decimal("0.0009"),
            daily_spread=Decimal("0.0012"),
            spread=Decimal("0.0004"),
            expected_daily_profit=Decimal("12.00"),
            annualized_apr=Decimal("43.8"),
            next_funding_time=datetime.now(timezone.utc) + timedelta(minutes=2),
            seconds_to_funding=120.0,
            detected_at=datetime.now(timezone.utc),
        )
        assert opp2.is_urgent is True

    def test_opportunity_mixed_intervals(self):
        """Test opportunity with different funding intervals (e.g., Binance 8h vs dYdX 1h)."""
        # Raw rates: long=-0.005% (1h dYdX), short=0.01% (8h Binance)
        # Daily rates: long=-0.12% (24 periods), short=0.03% (3 periods)
        # Daily spread: 0.15%
        opp = ArbitrageOpportunity(
            symbol="BTC/USDT:USDT",
            long_exchange="dydx",
            short_exchange="binance",
            long_interval_hours=1,  # dYdX hourly funding
            short_interval_hours=8,  # Binance 8h funding
            long_rate=Decimal("-0.00005"),  # -0.005%
            short_rate=Decimal("0.0001"),  # 0.01%
            long_daily_rate=Decimal("-0.0012"),  # -0.00005 * 24 = -0.12%
            short_daily_rate=Decimal("0.0003"),  # 0.0001 * 3 = 0.03%
            daily_spread=Decimal("0.0015"),  # 0.15% daily
            spread=Decimal("0.00015"),  # Raw spread (meaningless for mixed intervals)
            expected_daily_profit=Decimal("150.00"),  # $100k * 0.15%
            annualized_apr=Decimal("54.75"),  # 0.15% * 365
            next_funding_time=datetime.now(timezone.utc) + timedelta(minutes=30),
            seconds_to_funding=1800.0,
            detected_at=datetime.now(timezone.utc),
        )

        assert opp.long_interval_hours == 1
        assert opp.short_interval_hours == 8
        assert float(opp.daily_spread) == pytest.approx(0.0015)
        assert float(opp.spread_percent) == pytest.approx(0.15)
