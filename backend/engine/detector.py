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
    """
    symbol: str

    # Which exchanges to use
    long_exchange: str  # Exchange to go LONG (lower funding rate)
    short_exchange: str  # Exchange to go SHORT (higher funding rate)

    # Funding rates
    long_rate: Decimal  # Rate on long exchange
    short_rate: Decimal  # Rate on short exchange
    spread: Decimal  # short_rate - long_rate (positive = profitable)

    # Profitability estimates
    expected_profit_per_funding: Decimal  # Expected profit per funding event
    expected_daily_profit: Decimal  # Assuming 3 funding periods per day
    annualized_apr: Decimal  # Annual percentage rate

    # Timing
    next_funding_time: datetime
    seconds_to_funding: float

    # Metadata
    detected_at: datetime

    @property
    def spread_percent(self) -> Decimal:
        """Spread as percentage."""
        return self.spread * Decimal("100")

    @property
    def is_urgent(self) -> bool:
        """Check if opportunity is urgent (less than 5 minutes to funding)."""
        return self.seconds_to_funding < 300

    def __repr__(self) -> str:
        return (
            f"<ArbitrageOpportunity("
            f"symbol={self.symbol}, "
            f"spread={self.spread_percent:.4f}%, "
            f"long={self.long_exchange}, "
            f"short={self.short_exchange})>"
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
        Calculate dynamic spread threshold based on position size.

        Formula: threshold = base + (per_10k * size / 10000)

        Args:
            position_size_usd: Position size in USD

        Returns:
            Minimum spread required for profitability
        """
        base = self.config.min_spread_base
        per_10k = self.config.min_spread_per_10k
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

        Args:
            rates: Dict of exchange -> symbol -> FundingRate
            position_size_usd: Position size for threshold calculation
            min_seconds_to_funding: Minimum time to funding to consider

        Returns:
            List of opportunities sorted by spread (highest first)
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

            # Find the best long (lowest rate) and short (highest rate)
            sorted_rates = sorted(
                symbol_rates.items(),
                key=lambda x: x[1].rate,
            )

            long_exchange, long_rate_obj = sorted_rates[0]  # Lowest rate
            short_exchange, short_rate_obj = sorted_rates[-1]  # Highest rate

            spread = short_rate_obj.rate - long_rate_obj.rate

            # Skip if spread is below threshold
            if spread < threshold:
                continue

            # Check time to funding
            next_funding = min(
                long_rate_obj.next_funding_time,
                short_rate_obj.next_funding_time,
            )
            seconds_to_funding = (next_funding - datetime.now(timezone.utc)).total_seconds()

            if seconds_to_funding < min_seconds_to_funding:
                continue

            # Calculate profitability
            expected_profit = position_size_usd * spread
            fees = self.calculate_fees(position_size_usd, long_exchange, short_exchange)
            net_profit = expected_profit - fees

            # Skip if not profitable after fees
            if net_profit <= 0:
                continue

            # Calculate daily and annualized returns
            daily_profit = net_profit * 3  # Assuming 3 funding periods per day
            annualized = (daily_profit / position_size_usd) * 365 * 100  # APR %

            opportunities.append(ArbitrageOpportunity(
                symbol=symbol,
                long_exchange=long_exchange,
                short_exchange=short_exchange,
                long_rate=long_rate_obj.rate,
                short_rate=short_rate_obj.rate,
                spread=spread,
                expected_profit_per_funding=net_profit,
                expected_daily_profit=daily_profit,
                annualized_apr=annualized,
                next_funding_time=next_funding,
                seconds_to_funding=seconds_to_funding,
                detected_at=datetime.now(timezone.utc),
            ))

        # Sort by spread (highest first) - greedy approach
        opportunities.sort(key=lambda x: x.spread, reverse=True)

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

        Args:
            rates: Current funding rates
            symbol: Position symbol
            long_exchange: Current long exchange
            short_exchange: Current short exchange

        Returns:
            Tuple of (should_keep, current_spread, reason)
        """
        # Get current rates
        long_rate = rates.get(long_exchange, {}).get(symbol)
        short_rate = rates.get(short_exchange, {}).get(symbol)

        if not long_rate or not short_rate:
            return False, Decimal("0"), "Missing rate data"

        current_spread = short_rate.rate - long_rate.rate

        # Check if spread has inverted beyond tolerance
        if current_spread < self.config.negative_spread_tolerance:
            return False, current_spread, f"Spread inverted: {current_spread}"

        # Check if spread is still positive
        if current_spread > 0:
            return True, current_spread, "Spread still positive"

        # Spread is slightly negative but within tolerance
        return True, current_spread, "Within negative tolerance"

    @property
    def last_opportunities(self) -> List[ArbitrageOpportunity]:
        """Get last detected opportunities."""
        return self._last_opportunities.copy()
