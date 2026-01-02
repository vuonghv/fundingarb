"""
Order execution engine.

Handles the execution of hedged positions with proper
leg ordering and failure recovery.
"""

import asyncio
import time
from dataclasses import dataclass
from decimal import Decimal
from typing import Dict, Optional

from ..config.schema import TradingConfig
from ..exchanges.base import ExchangeAdapter, CircuitBreakerOpenError
from ..exchanges.types import (
    Order,
    OrderResult,
    OrderSide,
    OrderType,
)
from ..utils.logging import get_logger
from .detector import ArbitrageOpportunity

logger = get_logger(__name__)


@dataclass
class ExecutionResult:
    """Result of position entry or exit execution."""
    success: bool
    long_order: Optional[OrderResult]
    short_order: Optional[OrderResult]
    error_message: Optional[str] = None
    execution_time_ms: int = 0


class ExecutionEngine:
    """
    Handles order execution for arbitrage positions.

    Key features:
    - Executes lower liquidity exchange first (less likely to fail)
    - Immediately closes first leg if second leg fails
    - Uses limit orders at mid-price with configurable timeout
    """

    def __init__(
        self,
        exchanges: Dict[str, ExchangeAdapter],
        config: TradingConfig,
    ):
        """
        Initialize execution engine.

        Args:
            exchanges: Dict of connected exchange adapters
            config: Trading configuration
        """
        self.exchanges = exchanges
        self.config = config

        # Track pending orders
        self._pending_orders: Dict[str, OrderResult] = {}

    async def execute_entry(
        self,
        opportunity: ArbitrageOpportunity,
        size_usd: Decimal,
    ) -> ExecutionResult:
        """
        Execute hedged position entry.

        Process:
        1. Get orderbooks for both exchanges
        2. Determine execution order (lower liquidity first)
        3. Execute first leg with limit order
        4. If first leg fills, execute second leg
        5. If second leg fails, immediately close first leg

        Args:
            opportunity: Arbitrage opportunity to execute
            size_usd: Position size in USD

        Returns:
            ExecutionResult with order details
        """
        start_time = time.time()

        logger.info(
            "executing_entry",
            symbol=opportunity.symbol,
            long_exchange=opportunity.long_exchange,
            short_exchange=opportunity.short_exchange,
            size_usd=float(size_usd),
            spread=float(opportunity.spread),
        )

        try:
            # Set leverage on both exchanges
            await self._set_leverage(
                opportunity.symbol,
                opportunity.long_exchange,
                opportunity.short_exchange,
            )

            # Get orderbooks to determine execution order and prices
            long_book = await self.exchanges[opportunity.long_exchange].get_orderbook(
                opportunity.symbol
            )
            short_book = await self.exchanges[opportunity.short_exchange].get_orderbook(
                opportunity.symbol
            )

            # Determine which exchange has lower liquidity (execute first)
            long_depth = long_book.get_depth("ask", 5)  # We buy, so look at asks
            short_depth = short_book.get_depth("bid", 5)  # We sell, so look at bids

            if long_depth <= short_depth:
                # Long exchange has lower liquidity - execute long first
                first_exchange = opportunity.long_exchange
                first_side = OrderSide.BUY
                first_book = long_book
                second_exchange = opportunity.short_exchange
                second_side = OrderSide.SELL
                second_book = short_book
            else:
                # Short exchange has lower liquidity - execute short first
                first_exchange = opportunity.short_exchange
                first_side = OrderSide.SELL
                first_book = short_book
                second_exchange = opportunity.long_exchange
                second_side = OrderSide.BUY
                second_book = long_book

            # Calculate sizes in contracts
            first_price = first_book.mid_price
            second_price = second_book.mid_price

            if first_price is None or second_price is None:
                logger.warning(
                    "orderbook_missing_price",
                    symbol=opportunity.symbol,
                    first_exchange=first_exchange,
                    first_price=first_price,
                    second_exchange=second_exchange,
                    second_price=second_price,
                )
                return ExecutionResult(
                    success=False,
                    long_order=None,
                    short_order=None,
                    error_message="Orderbook missing price data (empty bids or asks)",
                    execution_time_ms=int((time.time() - start_time) * 1000),
                )

            first_size = size_usd / first_price
            second_size = size_usd / second_price

            # Execute first leg
            logger.info(
                "executing_first_leg",
                exchange=first_exchange,
                side=first_side.value,
                size=float(first_size),
                price=float(first_price),
            )

            first_result = await self._execute_with_timeout(
                first_exchange,
                opportunity.symbol,
                first_side,
                first_size,
                first_price,
            )

            if not first_result or not first_result.is_filled:
                logger.warning(
                    "first_leg_failed",
                    exchange=first_exchange,
                    result=first_result,
                )
                return ExecutionResult(
                    success=False,
                    long_order=None,
                    short_order=None,
                    error_message="First leg failed to fill",
                    execution_time_ms=int((time.time() - start_time) * 1000),
                )

            logger.info(
                "first_leg_filled",
                exchange=first_exchange,
                filled_price=float(first_result.average_price or first_price),
                filled_size=float(first_result.filled_size),
            )

            # Execute second leg
            # Refresh orderbook for better price
            second_book = await self.exchanges[second_exchange].get_orderbook(
                opportunity.symbol
            )
            second_price = second_book.mid_price

            if second_price is None:
                logger.error(
                    "second_leg_orderbook_missing_price",
                    exchange=second_exchange,
                    symbol=opportunity.symbol,
                )
                # Close first leg since we can't proceed
                await self._emergency_close(
                    first_exchange,
                    opportunity.symbol,
                    first_side,
                    first_result.filled_size,
                )
                return ExecutionResult(
                    success=False,
                    long_order=first_result if first_side == OrderSide.BUY else None,
                    short_order=first_result if first_side == OrderSide.SELL else None,
                    error_message="Second leg orderbook missing price data, first leg closed",
                    execution_time_ms=int((time.time() - start_time) * 1000),
                )

            second_size = size_usd / second_price

            logger.info(
                "executing_second_leg",
                exchange=second_exchange,
                side=second_side.value,
                size=float(second_size),
                price=float(second_price),
            )

            second_result = await self._execute_with_timeout(
                second_exchange,
                opportunity.symbol,
                second_side,
                second_size,
                second_price,
            )

            if not second_result or not second_result.is_filled:
                # CRITICAL: Second leg failed - close first leg immediately
                logger.error(
                    "second_leg_failed_closing_first",
                    first_exchange=first_exchange,
                    second_exchange=second_exchange,
                )

                await self._emergency_close(
                    first_exchange,
                    opportunity.symbol,
                    first_side,
                    first_result.filled_size,
                )

                return ExecutionResult(
                    success=False,
                    long_order=first_result if first_side == OrderSide.BUY else None,
                    short_order=first_result if first_side == OrderSide.SELL else None,
                    error_message="Second leg failed, first leg closed",
                    execution_time_ms=int((time.time() - start_time) * 1000),
                )

            logger.info(
                "second_leg_filled",
                exchange=second_exchange,
                filled_price=float(second_result.average_price or second_price),
                filled_size=float(second_result.filled_size),
            )

            # Determine which result is long and which is short
            if first_side == OrderSide.BUY:
                long_order = first_result
                short_order = second_result
            else:
                long_order = second_result
                short_order = first_result

            return ExecutionResult(
                success=True,
                long_order=long_order,
                short_order=short_order,
                execution_time_ms=int((time.time() - start_time) * 1000),
            )

        except CircuitBreakerOpenError as e:
            logger.error("circuit_breaker_open", error=str(e))
            return ExecutionResult(
                success=False,
                long_order=None,
                short_order=None,
                error_message=f"Circuit breaker open: {e}",
                execution_time_ms=int((time.time() - start_time) * 1000),
            )
        except Exception as e:
            logger.exception("execution_error", error=str(e))
            return ExecutionResult(
                success=False,
                long_order=None,
                short_order=None,
                error_message=str(e),
                execution_time_ms=int((time.time() - start_time) * 1000),
            )

    async def execute_exit(
        self,
        symbol: str,
        long_exchange: str,
        short_exchange: str,
        long_size: Decimal,
        short_size: Decimal,
    ) -> ExecutionResult:
        """
        Execute position exit (close both legs).

        Uses simultaneous market orders for fast exit.

        Args:
            symbol: Trading pair symbol
            long_exchange: Exchange with long position
            short_exchange: Exchange with short position
            long_size: Long position size
            short_size: Short position size

        Returns:
            ExecutionResult with close order details
        """
        start_time = time.time()

        logger.info(
            "executing_exit",
            symbol=symbol,
            long_exchange=long_exchange,
            short_exchange=short_exchange,
            long_size=float(long_size),
            short_size=float(short_size),
        )

        try:
            # Close both legs simultaneously (fire and forget)
            long_task = asyncio.create_task(
                self._close_position(long_exchange, symbol, OrderSide.SELL, long_size)
            )
            short_task = asyncio.create_task(
                self._close_position(short_exchange, symbol, OrderSide.BUY, short_size)
            )

            long_result, short_result = await asyncio.gather(
                long_task, short_task, return_exceptions=True
            )

            # Handle any exceptions
            if isinstance(long_result, Exception):
                logger.error("long_close_failed", error=str(long_result))
                long_result = None
            if isinstance(short_result, Exception):
                logger.error("short_close_failed", error=str(short_result))
                short_result = None

            success = bool(long_result and short_result)

            return ExecutionResult(
                success=success,
                long_order=long_result if not isinstance(long_result, Exception) else None,
                short_order=short_result if not isinstance(short_result, Exception) else None,
                error_message=None if success else "One or both close orders failed",
                execution_time_ms=int((time.time() - start_time) * 1000),
            )

        except Exception as e:
            logger.exception("exit_execution_error", error=str(e))
            return ExecutionResult(
                success=False,
                long_order=None,
                short_order=None,
                error_message=str(e),
                execution_time_ms=int((time.time() - start_time) * 1000),
            )

    async def _execute_with_timeout(
        self,
        exchange: str,
        symbol: str,
        side: OrderSide,
        size: Decimal,
        price: Decimal,
    ) -> Optional[OrderResult]:
        """
        Execute a limit order with timeout.

        If order doesn't fill within timeout, cancel it.

        Args:
            exchange: Exchange name
            symbol: Trading pair
            side: Order side
            size: Order size
            price: Limit price

        Returns:
            OrderResult if filled, None if cancelled or failed
        """
        adapter = self.exchanges[exchange]

        # Place limit order
        order = Order(
            symbol=symbol,
            side=side,
            order_type=OrderType.LIMIT,
            size=size,
            price=price,
        )

        result = await adapter.place_order(order)

        if result.is_filled:
            return result

        # Wait for fill with timeout
        timeout = self.config.order_fill_timeout_seconds
        start = time.time()

        while time.time() - start < timeout:
            await asyncio.sleep(0.5)

            # Check order status
            result = await adapter.get_order(result.order_id, symbol)

            if result.is_filled:
                return result

            if not result.is_open:
                # Order was cancelled or rejected
                return None

        # Timeout - cancel order
        logger.warning(
            "order_timeout_cancelling",
            exchange=exchange,
            order_id=result.order_id,
            timeout=timeout,
        )

        await adapter.cancel_order(result.order_id, symbol)
        return None

    async def _close_position(
        self,
        exchange: str,
        symbol: str,
        side: OrderSide,
        size: Decimal,
    ) -> OrderResult:
        """
        Close a position with market order.

        Args:
            exchange: Exchange name
            symbol: Trading pair
            side: Close side (opposite of position side)
            size: Position size

        Returns:
            OrderResult for the close order
        """
        adapter = self.exchanges[exchange]

        order = Order(
            symbol=symbol,
            side=side,
            order_type=OrderType.MARKET,
            size=size,
            reduce_only=True,
        )

        return await adapter.place_order(order)

    async def _emergency_close(
        self,
        exchange: str,
        symbol: str,
        side: OrderSide,
        size: Decimal,
    ) -> None:
        """
        Emergency close of a position (market order).

        Used when second leg fails and first leg needs to be unwound.

        Args:
            exchange: Exchange name
            symbol: Trading pair
            side: Position side to close
            size: Position size
        """
        logger.warning(
            "emergency_close",
            exchange=exchange,
            symbol=symbol,
            side=side.value,
            size=float(size),
        )

        close_side = side.opposite

        try:
            await self._close_position(exchange, symbol, close_side, size)
            logger.info("emergency_close_completed", exchange=exchange, symbol=symbol)
        except Exception as e:
            logger.error(
                "emergency_close_failed",
                exchange=exchange,
                symbol=symbol,
                error=str(e),
            )

    async def _set_leverage(
        self,
        symbol: str,
        long_exchange: str,
        short_exchange: str,
    ) -> None:
        """Set leverage on both exchanges before trading."""
        for exchange in [long_exchange, short_exchange]:
            # Get leverage setting for this exchange/symbol
            leverage_config = self.config.leverage.get(exchange)
            if leverage_config:
                leverage = leverage_config.get_leverage(symbol)
            else:
                leverage = 5  # Default leverage

            try:
                await self.exchanges[exchange].set_leverage(symbol, leverage)
            except Exception as e:
                logger.warning(
                    "set_leverage_failed",
                    exchange=exchange,
                    symbol=symbol,
                    error=str(e),
                )

    @property
    def pending_orders_count(self) -> int:
        """Get count of pending orders."""
        return len(self._pending_orders)
