"""
Data types for exchange interactions.

These types provide a unified interface across all exchanges.
"""

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import List, Optional, Tuple


class OrderSide(Enum):
    """Order direction."""
    BUY = "BUY"
    SELL = "SELL"

    @property
    def opposite(self) -> "OrderSide":
        """Get the opposite side."""
        return OrderSide.SELL if self == OrderSide.BUY else OrderSide.BUY


class OrderType(Enum):
    """Order type."""
    LIMIT = "LIMIT"
    MARKET = "MARKET"


class OrderStatus(Enum):
    """Order status."""
    PENDING = "PENDING"
    OPEN = "OPEN"
    FILLED = "FILLED"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    CANCELLED = "CANCELLED"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"


class PositionSide(Enum):
    """Position side for tracking."""
    LONG = "LONG"
    SHORT = "SHORT"


@dataclass
class FundingRate:
    """
    Funding rate information for a perpetual contract.

    Funding is typically paid every 8 hours (Binance, Bybit).
    """
    exchange: str
    symbol: str
    rate: Decimal  # Current funding rate (e.g., 0.0001 = 0.01%)
    predicted_rate: Optional[Decimal]  # Predicted next rate (if available)
    next_funding_time: datetime  # When funding is next applied
    timestamp: datetime  # When this data was fetched
    interval_hours: int = 8  # Funding interval in hours (default: 8h)
    mark_price: Optional[Decimal] = None  # Mark price used for funding
    index_price: Optional[Decimal] = None  # Index price (spot reference)

    @property
    def rate_percent(self) -> Decimal:
        """Rate as a percentage."""
        return self.rate * Decimal("100")

    @property
    def annualized_rate(self) -> Decimal:
        """Annualized rate based on funding interval."""
        periods_per_day = Decimal(24) / Decimal(self.interval_hours)
        return self.rate * periods_per_day * Decimal(365) * Decimal(100)


@dataclass
class OrderBookLevel:
    """Single level in the order book."""
    price: Decimal
    size: Decimal


@dataclass
class OrderBook:
    """
    Order book snapshot for a symbol.

    Bids are sorted descending (best bid first).
    Asks are sorted ascending (best ask first).
    """
    exchange: str
    symbol: str
    bids: List[OrderBookLevel]  # Buy orders (descending by price)
    asks: List[OrderBookLevel]  # Sell orders (ascending by price)
    timestamp: datetime

    @property
    def best_bid(self) -> Optional[Decimal]:
        """Best bid price."""
        return self.bids[0].price if self.bids else None

    @property
    def best_ask(self) -> Optional[Decimal]:
        """Best ask price."""
        return self.asks[0].price if self.asks else None

    @property
    def mid_price(self) -> Optional[Decimal]:
        """Mid-market price."""
        if self.best_bid and self.best_ask:
            return (self.best_bid + self.best_ask) / 2
        return None

    @property
    def spread(self) -> Optional[Decimal]:
        """Bid-ask spread."""
        if self.best_bid and self.best_ask:
            return self.best_ask - self.best_bid
        return None

    @property
    def spread_percent(self) -> Optional[Decimal]:
        """Spread as percentage of mid price."""
        if self.mid_price and self.spread:
            return (self.spread / self.mid_price) * 100
        return None

    def get_depth(self, side: str, levels: int = 5) -> Decimal:
        """Get total size at top N levels."""
        book = self.bids if side == "bid" else self.asks
        return sum(level.size for level in book[:levels])


@dataclass
class Order:
    """
    Order to be placed on an exchange.
    """
    symbol: str
    side: OrderSide
    order_type: OrderType
    size: Decimal
    price: Optional[Decimal] = None  # Required for limit orders
    reduce_only: bool = False  # For closing positions
    client_order_id: Optional[str] = None  # For tracking

    def __post_init__(self):
        if self.order_type == OrderType.LIMIT and self.price is None:
            raise ValueError("Limit orders require a price")


@dataclass
class OrderResult:
    """
    Result of an order execution.
    """
    order_id: str
    client_order_id: Optional[str]
    exchange: str
    symbol: str
    side: OrderSide
    order_type: OrderType
    status: OrderStatus
    size: Decimal  # Original order size
    filled_size: Decimal  # Actually filled
    price: Optional[Decimal]  # Limit price (if applicable)
    average_price: Optional[Decimal]  # Average fill price
    fee: Decimal
    fee_currency: str
    timestamp: datetime
    raw: dict = field(default_factory=dict)  # Raw exchange response

    @property
    def is_filled(self) -> bool:
        return self.status == OrderStatus.FILLED

    @property
    def is_open(self) -> bool:
        return self.status in (OrderStatus.OPEN, OrderStatus.PARTIALLY_FILLED, OrderStatus.PENDING)

    @property
    def fill_ratio(self) -> Decimal:
        """Ratio of filled to ordered size."""
        if self.size == 0:
            return Decimal("0")
        return self.filled_size / self.size


@dataclass
class ExchangePosition:
    """
    Open position on an exchange.
    """
    exchange: str
    symbol: str
    side: PositionSide
    size: Decimal  # Position size in contracts
    entry_price: Decimal
    mark_price: Decimal
    liquidation_price: Optional[Decimal]
    unrealized_pnl: Decimal
    leverage: int
    margin_type: str  # "cross" or "isolated"
    timestamp: datetime

    @property
    def notional_value(self) -> Decimal:
        """Position notional value in quote currency."""
        return self.size * self.mark_price


@dataclass
class FeeTier:
    """
    User's fee tier on an exchange.
    """
    exchange: str
    tier: str  # e.g., "VIP1", "regular"
    maker_fee: Decimal  # e.g., 0.0002 = 0.02%
    taker_fee: Decimal  # e.g., 0.0004 = 0.04%
    timestamp: datetime

    @property
    def maker_fee_percent(self) -> Decimal:
        return self.maker_fee * 100

    @property
    def taker_fee_percent(self) -> Decimal:
        return self.taker_fee * 100


@dataclass
class ExchangeBalance:
    """
    Account balance for a currency.
    """
    currency: str
    total: Decimal
    free: Decimal  # Available for trading
    used: Decimal  # In open orders or positions

    @property
    def used_percent(self) -> Decimal:
        if self.total == 0:
            return Decimal("0")
        return (self.used / self.total) * 100
