"""
Bybit exchange adapter.

Supports USDT perpetual futures via ccxt.
"""

from datetime import datetime, timezone
from decimal import Decimal
from typing import Dict, List, Optional, Any

import ccxt.async_support as ccxt

from ..utils.logging import get_logger
from .base import ExchangeAdapter, ExchangeError, RateLimitError, InsufficientBalanceError
from .types import (
    FundingRate,
    OrderBook,
    OrderBookLevel,
    Order,
    OrderResult,
    ExchangePosition,
    FeeTier,
    ExchangeBalance,
    OrderSide,
    OrderType,
    OrderStatus,
    PositionSide,
)

logger = get_logger(__name__)


class BybitAdapter(ExchangeAdapter):
    """
    Bybit USDT Perpetual adapter using ccxt.

    Features:
    - USDT-margined linear perpetuals
    - Real-time funding rate updates
    - Proper rate limit handling
    """

    @property
    def name(self) -> str:
        return "bybit"

    async def connect(self) -> None:
        """Initialize Bybit connection."""
        logger.info("connecting_to_bybit", testnet=self.testnet)

        options = {
            "defaultType": "linear",  # USDT perpetual
            "adjustForTimeDifference": True,
        }

        if self.testnet:
            self._client = ccxt.bybit({
                "apiKey": self.api_key,
                "secret": self.api_secret,
                "sandbox": True,
                "options": options,
                "enableRateLimit": True,
                "rateLimit": 100,  # ms between requests
            })
        else:
            self._client = ccxt.bybit({
                "apiKey": self.api_key,
                "secret": self.api_secret,
                "options": options,
                "enableRateLimit": True,
                "rateLimit": 100,
            })

        # Load markets
        await self._client.load_markets()

        self._connected = True
        logger.info(
            "bybit_connected",
            testnet=self.testnet,
            markets_loaded=len(self._client.markets),
        )

    async def disconnect(self) -> None:
        """Close Bybit connections."""
        if self._client:
            await self._client.close()
            self._client = None

        self._connected = False
        logger.info("bybit_disconnected")

    async def get_funding_rate(self, symbol: str) -> FundingRate:
        """Get funding rate for a symbol."""
        symbol = self.normalize_symbol(symbol)

        async def _fetch():
            return await self._client.fetch_funding_rate(symbol)

        data = await self._execute_with_retry(_fetch)

        # Parse interval string (e.g., "8h") to hours
        interval_str = data.get("interval", "8h")
        interval_hours = int(interval_str.rstrip("h")) if interval_str else 8

        # Extract mark and index prices
        mark_price = Decimal(str(data["markPrice"])) if data.get("markPrice") else None
        index_price = Decimal(str(data["indexPrice"])) if data.get("indexPrice") else None

        return FundingRate(
            exchange=self.name,
            symbol=symbol,
            rate=Decimal(str(data["fundingRate"])),
            predicted_rate=Decimal(str(data.get("nextFundingRate"))) if data.get("nextFundingRate") else None,
            next_funding_time=datetime.fromtimestamp(
                data["fundingTimestamp"] / 1000, tz=timezone.utc
            ),
            timestamp=datetime.now(timezone.utc),
            interval_hours=interval_hours,
            mark_price=mark_price,
            index_price=index_price,
        )

    async def get_funding_rates(self, symbols: List[str]) -> Dict[str, FundingRate]:
        """Get funding rates for multiple symbols."""
        result = {}

        # Bybit requires fetching individually or using a different endpoint
        # For efficiency, we could use the tickers endpoint
        async def _fetch():
            return await self._client.fetch_tickers(symbols)

        try:
            tickers = await self._execute_with_retry(_fetch)

            for symbol in symbols:
                normalized = self.normalize_symbol(symbol)
                if normalized in tickers:
                    ticker = tickers[normalized]
                    # Fetch individual funding rate for accurate data
                    try:
                        rate = await self.get_funding_rate(normalized)
                        result[normalized] = rate
                    except Exception:
                        continue
        except Exception as e:
            logger.warning("bulk_funding_rates_failed", error=str(e))
            # Fall back to individual fetches
            for symbol in symbols:
                try:
                    rate = await self.get_funding_rate(symbol)
                    result[rate.symbol] = rate
                except Exception:
                    continue

        return result

    async def get_orderbook(self, symbol: str, depth: int = 10) -> OrderBook:
        """Get order book snapshot."""
        symbol = self.normalize_symbol(symbol)

        async def _fetch():
            return await self._client.fetch_order_book(symbol, limit=depth)

        data = await self._execute_with_retry(_fetch)

        return OrderBook(
            exchange=self.name,
            symbol=symbol,
            bids=[OrderBookLevel(Decimal(str(p)), Decimal(str(s))) for p, s in data["bids"]],
            asks=[OrderBookLevel(Decimal(str(p)), Decimal(str(s))) for p, s in data["asks"]],
            timestamp=datetime.now(timezone.utc),
        )

    async def place_order(self, order: Order) -> OrderResult:
        """Place an order on Bybit."""
        symbol = self.normalize_symbol(order.symbol)

        async def _place():
            side = "buy" if order.side == OrderSide.BUY else "sell"
            params = {}

            if order.reduce_only:
                params["reduceOnly"] = True

            if order.order_type == OrderType.LIMIT:
                result = await self._client.create_limit_order(
                    symbol, side, float(order.size), float(order.price), params
                )
            else:
                result = await self._client.create_market_order(
                    symbol, side, float(order.size), params
                )

            return result

        try:
            data = await self._execute_with_retry(_place)
        except ccxt.InsufficientFunds as e:
            raise InsufficientBalanceError(str(e))
        except ccxt.RateLimitExceeded as e:
            raise RateLimitError(str(e))
        except ccxt.BaseError as e:
            raise ExchangeError(str(e))

        return self._parse_order_result(data, symbol)

    async def cancel_order(self, order_id: str, symbol: str) -> bool:
        """Cancel an order."""
        symbol = self.normalize_symbol(symbol)

        async def _cancel():
            return await self._client.cancel_order(order_id, symbol)

        try:
            await self._execute_with_retry(_cancel)
            return True
        except ccxt.OrderNotFound:
            return False
        except Exception as e:
            logger.warning("cancel_order_failed", order_id=order_id, error=str(e))
            return False

    async def cancel_all_orders(self, symbol: Optional[str] = None) -> int:
        """Cancel all open orders."""
        if symbol:
            symbol = self.normalize_symbol(symbol)

        async def _cancel_all():
            return await self._client.cancel_all_orders(symbol)

        try:
            result = await self._execute_with_retry(_cancel_all)
            return len(result) if isinstance(result, list) else 0
        except Exception as e:
            logger.warning("cancel_all_orders_failed", error=str(e))
            return 0

    async def get_order(self, order_id: str, symbol: str) -> OrderResult:
        """Get order status."""
        symbol = self.normalize_symbol(symbol)

        async def _fetch():
            return await self._client.fetch_order(order_id, symbol)

        data = await self._execute_with_retry(_fetch)
        return self._parse_order_result(data, symbol)

    async def get_open_orders(self, symbol: Optional[str] = None) -> List[OrderResult]:
        """Get all open orders."""
        if symbol:
            symbol = self.normalize_symbol(symbol)

        async def _fetch():
            return await self._client.fetch_open_orders(symbol)

        data = await self._execute_with_retry(_fetch)
        return [self._parse_order_result(o, o["symbol"]) for o in data]

    async def get_positions(self) -> List[ExchangePosition]:
        """Get all open positions."""
        async def _fetch():
            return await self._client.fetch_positions()

        data = await self._execute_with_retry(_fetch)

        positions = []
        for pos in data:
            size = Decimal(str(pos.get("contracts", 0) or 0))
            if size == 0:
                continue

            positions.append(ExchangePosition(
                exchange=self.name,
                symbol=pos["symbol"],
                side=PositionSide.LONG if pos["side"] == "long" else PositionSide.SHORT,
                size=abs(size),
                entry_price=Decimal(str(pos.get("entryPrice", 0) or 0)),
                mark_price=Decimal(str(pos.get("markPrice", 0) or 0)),
                liquidation_price=Decimal(str(pos.get("liquidationPrice", 0) or 0)) if pos.get("liquidationPrice") else None,
                unrealized_pnl=Decimal(str(pos.get("unrealizedPnl", 0) or 0)),
                leverage=int(pos.get("leverage", 1) or 1),
                margin_type=pos.get("marginMode", "cross"),
                timestamp=datetime.now(timezone.utc),
            ))

        return positions

    async def get_position(self, symbol: str) -> Optional[ExchangePosition]:
        """Get position for a specific symbol."""
        symbol = self.normalize_symbol(symbol)
        positions = await self.get_positions()

        for pos in positions:
            if pos.symbol == symbol:
                return pos

        return None

    async def set_leverage(self, symbol: str, leverage: int) -> None:
        """Set leverage for a symbol."""
        symbol = self.normalize_symbol(symbol)

        async def _set():
            return await self._client.set_leverage(leverage, symbol)

        await self._execute_with_retry(_set)
        logger.info("leverage_set", exchange=self.name, symbol=symbol, leverage=leverage)

    async def get_balance(self, currency: str = "USDT") -> ExchangeBalance:
        """Get account balance."""
        async def _fetch():
            return await self._client.fetch_balance()

        data = await self._execute_with_retry(_fetch)

        if currency not in data:
            return ExchangeBalance(
                currency=currency,
                total=Decimal("0"),
                free=Decimal("0"),
                used=Decimal("0"),
            )

        balance = data[currency]
        return ExchangeBalance(
            currency=currency,
            total=Decimal(str(balance.get("total", 0) or 0)),
            free=Decimal(str(balance.get("free", 0) or 0)),
            used=Decimal(str(balance.get("used", 0) or 0)),
        )

    async def get_fee_tier(self) -> FeeTier:
        """Get user's fee tier."""
        # Bybit fee info is not directly available via ccxt
        # Return default rates, user can override in config
        return FeeTier(
            exchange=self.name,
            tier="standard",
            maker_fee=Decimal("0.0001"),  # 0.01%
            taker_fee=Decimal("0.0006"),  # 0.06%
            timestamp=datetime.now(timezone.utc),
        )

    def _parse_order_result(self, data: dict, symbol: str) -> OrderResult:
        """Parse ccxt order response to OrderResult."""
        status_map = {
            "open": OrderStatus.OPEN,
            "closed": OrderStatus.FILLED,
            "canceled": OrderStatus.CANCELLED,
            "cancelled": OrderStatus.CANCELLED,
            "expired": OrderStatus.EXPIRED,
            "rejected": OrderStatus.REJECTED,
        }

        return OrderResult(
            order_id=str(data["id"]),
            client_order_id=data.get("clientOrderId"),
            exchange=self.name,
            symbol=symbol,
            side=OrderSide.BUY if data["side"] == "buy" else OrderSide.SELL,
            order_type=OrderType.LIMIT if data["type"] == "limit" else OrderType.MARKET,
            status=status_map.get(data["status"], OrderStatus.PENDING),
            size=Decimal(str(data.get("amount", 0) or 0)),
            filled_size=Decimal(str(data.get("filled", 0) or 0)),
            price=Decimal(str(data["price"])) if data.get("price") else None,
            average_price=Decimal(str(data["average"])) if data.get("average") else None,
            fee=Decimal(str(data["fee"]["cost"])) if data.get("fee") else Decimal("0"),
            fee_currency=data.get("fee", {}).get("currency", "USDT"),
            timestamp=datetime.fromtimestamp(
                data["timestamp"] / 1000, tz=timezone.utc
            ) if data.get("timestamp") else datetime.now(timezone.utc),
            raw=data,
        )
