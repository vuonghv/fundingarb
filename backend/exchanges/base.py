"""
Abstract base class for exchange adapters.

All exchange implementations must inherit from this class
and implement the required methods.
"""

import asyncio
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from decimal import Decimal
from typing import Callable, Dict, List, Optional, Any

from ..utils.logging import get_logger
from .types import (
    FundingRate,
    OrderBook,
    Order,
    OrderResult,
    ExchangePosition,
    FeeTier,
    ExchangeBalance,
    OrderStatus,
)

logger = get_logger(__name__)


class CircuitBreakerOpenError(Exception):
    """Raised when circuit breaker is open due to consecutive failures."""
    pass


class ExchangeError(Exception):
    """Base exception for exchange-related errors."""
    pass


class RateLimitError(ExchangeError):
    """Raised when rate limit is exceeded."""
    pass


class InsufficientBalanceError(ExchangeError):
    """Raised when balance is insufficient for order."""
    pass


class ExchangeAdapter(ABC):
    """
    Abstract base class for exchange integrations.

    Features:
    - Unified interface for all exchanges
    - Circuit breaker for fault tolerance
    - Rate limit handling
    - WebSocket support for real-time data
    """

    # Circuit breaker settings
    CIRCUIT_BREAKER_THRESHOLD = 5
    CIRCUIT_BREAKER_RESET_TIME = 60  # seconds

    def __init__(
        self,
        api_key: str,
        api_secret: str,
        testnet: bool = False,
        rate_limit_buffer: float = 0.1,
    ):
        """
        Initialize exchange adapter.

        Args:
            api_key: API key
            api_secret: API secret
            testnet: Use testnet environment
            rate_limit_buffer: Buffer ratio for rate limits (0.1 = 10%)
        """
        self.api_key = api_key
        self.api_secret = api_secret
        self.testnet = testnet
        self.rate_limit_buffer = rate_limit_buffer

        # Connection state
        self._connected = False
        self._client: Any = None

        # Circuit breaker state
        self._consecutive_failures = 0
        self._circuit_breaker_open = False
        self._circuit_breaker_opened_at: Optional[datetime] = None

        # Request queue for rate limiting
        self._request_queue: asyncio.Queue = asyncio.Queue()
        self._rate_limit_task: Optional[asyncio.Task] = None

    @property
    @abstractmethod
    def name(self) -> str:
        """Exchange name identifier (e.g., 'binance', 'bybit')."""
        pass

    @property
    def is_connected(self) -> bool:
        """Check if exchange is connected."""
        return self._connected

    @property
    def is_testnet(self) -> bool:
        """Check if using testnet."""
        return self.testnet

    # ==================== Connection Management ====================

    @abstractmethod
    async def connect(self) -> None:
        """
        Initialize connection to exchange.

        This should:
        - Create the ccxt client
        - Load markets
        - Start WebSocket connections if needed
        """
        pass

    @abstractmethod
    async def disconnect(self) -> None:
        """
        Close all connections to exchange.

        This should:
        - Close WebSocket connections
        - Clean up resources
        """
        pass

    # ==================== Market Data ====================

    @abstractmethod
    async def get_funding_rate(self, symbol: str) -> FundingRate:
        """
        Get current and predicted funding rate for a symbol.

        Args:
            symbol: Trading pair symbol (e.g., 'BTC/USDT:USDT')

        Returns:
            FundingRate with current rate and next funding time
        """
        pass

    @abstractmethod
    async def get_funding_rates(self, symbols: List[str]) -> Dict[str, FundingRate]:
        """
        Get funding rates for multiple symbols.

        Args:
            symbols: List of trading pair symbols

        Returns:
            Dict mapping symbol to FundingRate
        """
        pass

    @abstractmethod
    async def get_orderbook(self, symbol: str, depth: int = 10) -> OrderBook:
        """
        Get order book snapshot.

        Args:
            symbol: Trading pair symbol
            depth: Number of levels to fetch

        Returns:
            OrderBook with bids and asks
        """
        pass

    # ==================== Trading ====================

    @abstractmethod
    async def place_order(self, order: Order) -> OrderResult:
        """
        Place an order on the exchange.

        Args:
            order: Order to place

        Returns:
            OrderResult with order status and fill information
        """
        pass

    @abstractmethod
    async def cancel_order(self, order_id: str, symbol: str) -> bool:
        """
        Cancel an open order.

        Args:
            order_id: Exchange order ID
            symbol: Trading pair symbol

        Returns:
            True if cancelled successfully
        """
        pass

    @abstractmethod
    async def cancel_all_orders(self, symbol: Optional[str] = None) -> int:
        """
        Cancel all open orders.

        Args:
            symbol: If provided, only cancel orders for this symbol

        Returns:
            Number of orders cancelled
        """
        pass

    @abstractmethod
    async def get_order(self, order_id: str, symbol: str) -> OrderResult:
        """
        Get order status.

        Args:
            order_id: Exchange order ID
            symbol: Trading pair symbol

        Returns:
            OrderResult with current status
        """
        pass

    @abstractmethod
    async def get_open_orders(self, symbol: Optional[str] = None) -> List[OrderResult]:
        """
        Get all open orders.

        Args:
            symbol: If provided, only get orders for this symbol

        Returns:
            List of open orders
        """
        pass

    # ==================== Position Management ====================

    @abstractmethod
    async def get_positions(self) -> List[ExchangePosition]:
        """
        Get all open positions.

        Returns:
            List of open positions
        """
        pass

    @abstractmethod
    async def get_position(self, symbol: str) -> Optional[ExchangePosition]:
        """
        Get position for a specific symbol.

        Args:
            symbol: Trading pair symbol

        Returns:
            Position if exists, None otherwise
        """
        pass

    @abstractmethod
    async def set_leverage(self, symbol: str, leverage: int) -> None:
        """
        Set leverage for a symbol.

        Args:
            symbol: Trading pair symbol
            leverage: Leverage to set
        """
        pass

    # ==================== Account ====================

    @abstractmethod
    async def get_balance(self, currency: str = "USDT") -> ExchangeBalance:
        """
        Get account balance.

        Args:
            currency: Currency to get balance for

        Returns:
            ExchangeBalance with total/free/used amounts
        """
        pass

    @abstractmethod
    async def get_fee_tier(self) -> FeeTier:
        """
        Get user's fee tier.

        Returns:
            FeeTier with maker/taker fees
        """
        pass

    # ==================== Circuit Breaker ====================

    def _check_circuit_breaker(self) -> None:
        """Check if circuit breaker is open and should block requests."""
        if not self._circuit_breaker_open:
            return

        # Check if enough time has passed to reset
        if self._circuit_breaker_opened_at:
            elapsed = (datetime.now(timezone.utc) - self._circuit_breaker_opened_at).total_seconds()
            if elapsed >= self.CIRCUIT_BREAKER_RESET_TIME:
                logger.info("circuit_breaker_reset", exchange=self.name)
                self._circuit_breaker_open = False
                self._consecutive_failures = 0
                return

        raise CircuitBreakerOpenError(
            f"{self.name} circuit breaker is open after {self._consecutive_failures} consecutive failures"
        )

    def _record_success(self) -> None:
        """Record a successful API call."""
        self._consecutive_failures = 0
        if self._circuit_breaker_open:
            logger.info("circuit_breaker_closed_on_success", exchange=self.name)
            self._circuit_breaker_open = False

    def _record_failure(self, error: Exception) -> None:
        """Record a failed API call."""
        self._consecutive_failures += 1
        logger.warning(
            "api_call_failed",
            exchange=self.name,
            consecutive_failures=self._consecutive_failures,
            error=str(error),
        )

        if self._consecutive_failures >= self.CIRCUIT_BREAKER_THRESHOLD:
            if not self._circuit_breaker_open:
                logger.error(
                    "circuit_breaker_opened",
                    exchange=self.name,
                    consecutive_failures=self._consecutive_failures,
                )
                self._circuit_breaker_open = True
                self._circuit_breaker_opened_at = datetime.now(timezone.utc)

    async def _execute_with_retry(
        self,
        operation: Callable,
        *args,
        max_retries: int = 3,
        retry_delay: float = 1.0,
        **kwargs,
    ) -> Any:
        """
        Execute an operation with retry logic and circuit breaker.

        Args:
            operation: Async function to execute
            max_retries: Maximum retry attempts
            retry_delay: Delay between retries (exponential backoff)

        Returns:
            Result of the operation

        Raises:
            CircuitBreakerOpenError: If circuit breaker is open
            Exception: If all retries fail
        """
        self._check_circuit_breaker()

        last_error = None
        for attempt in range(max_retries):
            try:
                result = await operation(*args, **kwargs)
                self._record_success()
                return result
            except RateLimitError as e:
                # Rate limit - always retry with backoff
                delay = retry_delay * (2 ** attempt)
                logger.warning(
                    "rate_limit_hit",
                    exchange=self.name,
                    attempt=attempt + 1,
                    delay=delay,
                )
                await asyncio.sleep(delay)
                last_error = e
            except Exception as e:
                self._record_failure(e)
                last_error = e

                if attempt < max_retries - 1:
                    delay = retry_delay * (2 ** attempt)
                    logger.warning(
                        "api_call_retry",
                        exchange=self.name,
                        attempt=attempt + 1,
                        max_retries=max_retries,
                        delay=delay,
                        error=str(e),
                    )
                    await asyncio.sleep(delay)

        raise last_error if last_error else Exception("Unknown error")

    # ==================== Utility Methods ====================

    def normalize_symbol(self, symbol: str) -> str:
        """
        Normalize symbol to ccxt unified format.

        Args:
            symbol: Symbol in any format

        Returns:
            Symbol in ccxt format (e.g., 'BTC/USDT:USDT')
        """
        # Already in ccxt format
        if ":" in symbol:
            return symbol

        # Convert BTC-USDT or BTCUSDT to BTC/USDT:USDT
        symbol = symbol.replace("-", "/")
        if "/" not in symbol:
            # Assume USDT pair
            symbol = f"{symbol[:-4]}/{symbol[-4:]}"

        # Add perpetual marker
        if not symbol.endswith(":USDT"):
            symbol = f"{symbol}:USDT"

        return symbol

    def __repr__(self) -> str:
        return f"<{self.__class__.__name__}(testnet={self.testnet}, connected={self._connected})>"
