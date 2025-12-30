"""Exchange integration module using ccxt."""

from .types import (
    FundingRate,
    OrderBook,
    Order,
    OrderResult,
    ExchangePosition,
    FeeTier,
    OrderSide,
    OrderType,
    OrderStatus,
)
from .base import ExchangeAdapter, CircuitBreakerOpenError
from .factory import create_exchange, create_exchanges
from .binance import BinanceAdapter
from .bybit import BybitAdapter

__all__ = [
    # Types
    "FundingRate",
    "OrderBook",
    "Order",
    "OrderResult",
    "ExchangePosition",
    "FeeTier",
    "OrderSide",
    "OrderType",
    "OrderStatus",
    # Base
    "ExchangeAdapter",
    "CircuitBreakerOpenError",
    # Factory
    "create_exchange",
    "create_exchanges",
    # Adapters
    "BinanceAdapter",
    "BybitAdapter",
]
