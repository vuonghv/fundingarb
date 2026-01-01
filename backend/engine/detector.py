"""
Arbitrage opportunity detector.

Analyzes funding rates across exchanges to find profitable
arbitrage opportunities.
"""

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Dict, List, Optional, Tuple

from ..config.schema import TradingConfig
from ..exchanges.types import FundingRate, FeeTier
from ..utils.logging import get_logger

logger = get_logger(__name__)


@dataclass
class ArbitrageOpportunity:
    """
    Represents an arbitrage opportunity.

    The strategy is:
    - LONG on the exchange with lower/negative funding rate (pay less or receive)
    - SHORT on the exchange with higher/positive funding rate (receive more)

    All spread and profit calculations are normalized to a DAILY basis to correctly
    compare opportunities across exchanges with different funding intervals
    (e.g., Binance 8h vs dYdX 1h).
    """
    symbol: str

    # Which exchanges to use
    long_exchange: str  # Exchange to go LONG (lower funding rate)
    short_exchange: str  # Exchange to go SHORT (higher funding rate)

    # Funding intervals (hours) for each exchange
    long_interval_hours: int  # e.g., 8 for Binance, 1 for dYdX
    short_interval_hours: int

    # Raw funding rates (per their respective intervals)
    long_rate: Decimal  # Rate on long exchange (per interval)
    short_rate: Decimal  # Rate on short exchange (per interval)

    # Daily normalized rates and spread
    long_daily_rate: Decimal  # long_rate * (24 / long_interval_hours)
    short_daily_rate: Decimal  # short_rate * (24 / short_interval_hours)
    daily_spread: Decimal  # short_daily_rate - long_daily_rate

    # Legacy spread field (kept for backwards compatibility)
    # Note: This is the raw spread, prefer daily_spread for calculations
    spread: Decimal  # short_rate - long_rate (raw, not normalized)

    # Profitability estimates (all daily normalized)
    expected_daily_profit: Decimal  # Daily profit based on normalized spread
    annualized_apr: Decimal  # Annual percentage rate

    # Timing
    next_funding_time: datetime
    seconds_to_funding: float

    # Metadata
    detected_at: datetime

    @property
    def spread_percent(self) -> Decimal:
        """Daily spread as percentage."""
        return self.daily_spread * Decimal("100")

    @property
    def raw_spread_percent(self) -> Decimal:
        """Raw (non-normalized) spread as percentage."""
        return self.spread * Decimal("100")

    @property
    def is_urgent(self) -> bool:
        """Check if opportunity is urgent (less than 5 minutes to funding)."""
        return self.seconds_to_funding < 300

    def __repr__(self) -> str:
        return (
            f"<ArbitrageOpportunity("
            f"symbol={self.symbol}, "
            f"daily_spread={self.spread_percent:.4f}%, "
            f"long={self.long_exchange}({self.long_interval_hours}h), "
            f"short={self.short_exchange}({self.short_interval_hours}h))>"
        )


class ArbitrageDetector:
    """
    Detects funding rate arbitrage opportunities.

    Features:
    - Dynamic threshold based on position size
    - Fee-aware profitability calculation
    - Priority scoring for multiple opportunities
    """

    def __init__(
        self,
        config: TradingConfig,
        fee_tiers: Optional[Dict[str, FeeTier]] = None,
    ):
        """
        Initialize detector.

        Args:
            config: Trading configuration
            fee_tiers: Optional dict of exchange -> FeeTier for accurate fee calculation
        """
        self.config = config
        self.fee_tiers = fee_tiers or {}

        # Cache for optimization
        self._last_opportunities: List[ArbitrageOpportunity] = []

    def calculate_threshold(self, position_size_usd: Decimal) -> Decimal:
        """
        Calculate dynamic daily spread threshold based on position size.

        Formula: threshold = base + (per_10k * size / 10000)

        The threshold is compared against the daily normalized spread,
        ensuring correct comparison across exchanges with different intervals.

        Args:
            position_size_usd: Position size in USD

        Returns:
            Minimum daily spread required for profitability
        """
        base = self.config.min_daily_spread_base
        per_10k = self.config.min_daily_spread_per_10k
        return base + (per_10k * (position_size_usd / Decimal("10000")))

    def calculate_fees(
        self,
        position_size_usd: Decimal,
        long_exchange: str,
        short_exchange: str,
    ) -> Decimal:
        """
        Calculate total fees for opening and closing a position.

        Args:
            position_size_usd: Position size
            long_exchange: Long leg exchange
            short_exchange: Short leg exchange

        Returns:
            Total fees in USD
        """
        total_fees = Decimal("0")

        for exchange in [long_exchange, short_exchange]:
            if exchange in self.fee_tiers:
                fee_rate = self.fee_tiers[exchange].taker_fee
            else:
                # Default conservative fee estimate
                fee_rate = Decimal("0.0004")  # 0.04%

            # Opening and closing = 2 trades per leg
            total_fees += position_size_usd * fee_rate * 2

        return total_fees

    def find_opportunities(
        self,
        rates: Dict[str, Dict[str, FundingRate]],
        position_size_usd: Decimal,
        min_seconds_to_funding: float = 60,
    ) -> List[ArbitrageOpportunity]:
        """
        Find all arbitrage opportunities above threshold.

        All rate comparisons and profit calculations are normalized to a DAILY basis
        to correctly handle exchanges with different funding intervals (e.g., 8h vs 1h).

        Args:
            rates: Dict of exchange -> symbol -> FundingRate
            position_size_usd: Position size for threshold calculation
            min_seconds_to_funding: Minimum time to funding to consider

        Returns:
            List of opportunities sorted by daily spread (highest first)
        """
        threshold = self.calculate_threshold(position_size_usd)
        opportunities: List[ArbitrageOpportunity] = []

        # Get all unique symbols across exchanges
        all_symbols = set()
        for exchange_rates in rates.values():
            all_symbols.update(exchange_rates.keys())

        # Check each symbol for arbitrage
        for symbol in all_symbols:
            # Get rates for this symbol across all exchanges
            symbol_rates: Dict[str, FundingRate] = {}
            for exchange, exchange_rates in rates.items():
                if symbol in exchange_rates:
                    symbol_rates[exchange] = exchange_rates[symbol]

            # Need at least 2 exchanges
            if len(symbol_rates) < 2:
                continue

            # Find the best long (lowest DAILY rate) and short (highest DAILY rate)
            # Using daily_rate ensures correct comparison across different intervals
            sorted_rates = sorted(
                symbol_rates.items(),
                key=lambda x: x[1].daily_rate,  # Sort by daily normalized rate
            )

            long_exchange, long_rate_obj = sorted_rates[0]  # Lowest daily rate
            short_exchange, short_rate_obj = sorted_rates[-1]  # Highest daily rate

            # Calculate daily normalized rates
            long_daily_rate = long_rate_obj.daily_rate
            short_daily_rate = short_rate_obj.daily_rate
            daily_spread = short_daily_rate - long_daily_rate

            # Raw spread (for backwards compatibility)
            raw_spread = short_rate_obj.rate - long_rate_obj.rate

            # Skip if daily spread is below threshold
            # Note: threshold is now compared against daily spread
            if daily_spread < threshold:
                continue

            # Check time to funding
            next_funding = min(
                long_rate_obj.next_funding_time,
                short_rate_obj.next_funding_time,
            )
            seconds_to_funding = (next_funding - datetime.now(timezone.utc)).total_seconds()

            if seconds_to_funding < min_seconds_to_funding:
                continue

            # Calculate daily profitability using normalized daily spread
            expected_daily_profit = position_size_usd * daily_spread

            # Fees are for entry+exit, amortized over expected holding period
            # For simplicity, we show gross daily profit; fees deducted at execution
            fees = self.calculate_fees(position_size_usd, long_exchange, short_exchange)

            # For display, we can show profit after amortized fees
            # Assuming average holding period of 7 days for fee amortization
            daily_fee_amortized = fees / Decimal("7")
            net_daily_profit = expected_daily_profit - daily_fee_amortized

            # Skip if not profitable after amortized fees
            if net_daily_profit <= 0:
                continue

            # Calculate APR from daily profit
            annualized = (net_daily_profit / position_size_usd) * Decimal("365") * Decimal("100")

            opportunities.append(ArbitrageOpportunity(
                symbol=symbol,
                long_exchange=long_exchange,
                short_exchange=short_exchange,
                long_interval_hours=long_rate_obj.interval_hours,
                short_interval_hours=short_rate_obj.interval_hours,
                long_rate=long_rate_obj.rate,
                short_rate=short_rate_obj.rate,
                long_daily_rate=long_daily_rate,
                short_daily_rate=short_daily_rate,
                daily_spread=daily_spread,
                spread=raw_spread,  # Legacy field
                expected_daily_profit=net_daily_profit,
                annualized_apr=annualized,
                next_funding_time=next_funding,
                seconds_to_funding=seconds_to_funding,
                detected_at=datetime.now(timezone.utc),
            ))

        # Sort by daily spread (highest first) - greedy approach
        opportunities.sort(key=lambda x: x.daily_spread, reverse=True)

        self._last_opportunities = opportunities
        return opportunities

    def find_best_opportunity(
        self,
        rates: Dict[str, Dict[str, FundingRate]],
        position_size_usd: Decimal,
        excluded_pairs: Optional[List[str]] = None,
    ) -> Optional[ArbitrageOpportunity]:
        """
        Find the single best arbitrage opportunity.

        Args:
            rates: Funding rates from all exchanges
            position_size_usd: Position size
            excluded_pairs: Pairs to exclude (e.g., already have position)

        Returns:
            Best opportunity or None
        """
        opportunities = self.find_opportunities(rates, position_size_usd)

        if excluded_pairs:
            opportunities = [o for o in opportunities if o.symbol not in excluded_pairs]

        return opportunities[0] if opportunities else None

    def evaluate_existing_position(
        self,
        rates: Dict[str, Dict[str, FundingRate]],
        symbol: str,
        long_exchange: str,
        short_exchange: str,
    ) -> Tuple[bool, Decimal, str]:
        """
        Evaluate if an existing position should be kept or closed.

        Uses daily normalized spread for comparison to correctly handle
        exchanges with different funding intervals.

        Args:
            rates: Current funding rates
            symbol: Position symbol
            long_exchange: Current long exchange
            short_exchange: Current short exchange

        Returns:
            Tuple of (should_keep, current_daily_spread, reason)
        """
        # Get current rates
        long_rate = rates.get(long_exchange, {}).get(symbol)
        short_rate = rates.get(short_exchange, {}).get(symbol)

        if not long_rate or not short_rate:
            return False, Decimal("0"), "Missing rate data"

        # Use daily normalized spread for comparison
        current_daily_spread = short_rate.daily_rate - long_rate.daily_rate

        # Check if spread has inverted beyond tolerance
        # Note: tolerance is compared against daily spread
        if current_daily_spread < self.config.negative_spread_tolerance:
            return False, current_daily_spread, f"Daily spread inverted: {current_daily_spread}"

        # Check if spread is still positive
        if current_daily_spread > 0:
            return True, current_daily_spread, "Daily spread still positive"

        # Spread is slightly negative but within tolerance
        return True, current_daily_spread, "Within negative tolerance"

    @property
    def last_opportunities(self) -> List[ArbitrageOpportunity]:
        """Get last detected opportunities."""
        return self._last_opportunities.copy()
