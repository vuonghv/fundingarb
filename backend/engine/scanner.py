"""
Funding rate scanner.

Monitors funding rates across all configured exchanges
and triggers callbacks when rates update.
"""

import asyncio
from datetime import datetime, timezone
from typing import Callable, Dict, List, Optional, Set

from ..exchanges.base import ExchangeAdapter
from ..exchanges.types import FundingRate
from ..utils.logging import get_logger

logger = get_logger(__name__)


class FundingRateScanner:
    """
    Scans funding rates across exchanges.

    Features:
    - Event-driven updates via callbacks
    - Caches latest rates for quick access
    - Handles exchange disconnections gracefully
    """

    def __init__(self, exchanges: Dict[str, ExchangeAdapter]):
        """
        Initialize scanner.

        Args:
            exchanges: Dict of connected exchange adapters
        """
        self.exchanges = exchanges
        self._running = False

        # Cache: exchange -> symbol -> FundingRate
        self._rates: Dict[str, Dict[str, FundingRate]] = {}

        # Callbacks to notify on rate updates
        self._callbacks: List[Callable[[Dict[str, Dict[str, FundingRate]]], None]] = []

        # Track which symbols we're monitoring
        self._symbols: Set[str] = set()

        # Last update timestamp per exchange
        self._last_update: Dict[str, datetime] = {}

    async def start(self, symbols: List[str]) -> None:
        """
        Start scanning funding rates.

        Args:
            symbols: List of symbols to monitor
        """
        if self._running:
            logger.warning("scanner_already_running")
            return

        self._symbols = set(symbols)
        self._running = True

        logger.info(
            "scanner_starting",
            symbols=list(symbols),
            exchanges=list(self.exchanges.keys()),
        )

        # Subscribe to each exchange
        for name, exchange in self.exchanges.items():
            try:
                await exchange.subscribe_funding_rates(
                    list(self._symbols),
                    callback=lambda rate, exch=name: self._on_rate_update(exch, rate),
                )
                logger.info("subscribed_to_funding_rates", exchange=name)
            except Exception as e:
                logger.error("subscription_failed", exchange=name, error=str(e))

        # Initial fetch to populate cache
        await self._fetch_all_rates()

    async def stop(self) -> None:
        """Stop scanning."""
        self._running = False
        logger.info("scanner_stopped")

    def _on_rate_update(self, exchange: str, rate: FundingRate) -> None:
        """Handle incoming funding rate update."""
        if exchange not in self._rates:
            self._rates[exchange] = {}

        self._rates[exchange][rate.symbol] = rate
        self._last_update[exchange] = datetime.now(timezone.utc)

        logger.debug(
            "rate_updated",
            exchange=exchange,
            symbol=rate.symbol,
            rate=float(rate.rate),
            next_funding=rate.next_funding_time.isoformat(),
        )

        # Notify callbacks
        self._notify_callbacks()

    def _notify_callbacks(self) -> None:
        """Notify all registered callbacks of rate update."""
        for callback in self._callbacks:
            try:
                callback(self._rates)
            except Exception as e:
                logger.error("callback_error", error=str(e))

    async def _fetch_all_rates(self) -> None:
        """Fetch current rates from all exchanges."""
        for name, exchange in self.exchanges.items():
            try:
                rates = await exchange.get_funding_rates(list(self._symbols))
                for symbol, rate in rates.items():
                    self._on_rate_update(name, rate)
            except Exception as e:
                logger.error("fetch_rates_failed", exchange=name, error=str(e))

    def on_update(self, callback: Callable[[Dict[str, Dict[str, FundingRate]]], None]) -> None:
        """
        Register a callback for rate updates.

        Args:
            callback: Function called with current rates dict
        """
        self._callbacks.append(callback)

    def get_rates(self) -> Dict[str, Dict[str, FundingRate]]:
        """Get all cached funding rates."""
        return self._rates.copy()

    def get_rates_for_symbol(self, symbol: str) -> Dict[str, FundingRate]:
        """
        Get funding rates for a specific symbol across all exchanges.

        Args:
            symbol: Trading pair symbol

        Returns:
            Dict mapping exchange name to FundingRate
        """
        result = {}
        for exchange, rates in self._rates.items():
            if symbol in rates:
                result[exchange] = rates[symbol]
        return result

    def get_rate(self, exchange: str, symbol: str) -> Optional[FundingRate]:
        """
        Get funding rate for a specific exchange and symbol.

        Args:
            exchange: Exchange name
            symbol: Trading pair symbol

        Returns:
            FundingRate if available, None otherwise
        """
        return self._rates.get(exchange, {}).get(symbol)

    def get_next_funding_time(self, symbol: str) -> Optional[datetime]:
        """
        Get the earliest next funding time for a symbol.

        Args:
            symbol: Trading pair symbol

        Returns:
            Earliest next funding time across all exchanges
        """
        times = []
        for rates in self._rates.values():
            if symbol in rates:
                times.append(rates[symbol].next_funding_time)

        return min(times) if times else None

    def get_time_to_funding(self, symbol: str) -> Optional[float]:
        """
        Get seconds until next funding for a symbol.

        Args:
            symbol: Trading pair symbol

        Returns:
            Seconds until next funding, None if unknown
        """
        next_time = self.get_next_funding_time(symbol)
        if next_time:
            delta = next_time - datetime.now(timezone.utc)
            return delta.total_seconds()
        return None

    @property
    def is_running(self) -> bool:
        """Check if scanner is running."""
        return self._running

    @property
    def monitored_symbols(self) -> Set[str]:
        """Get set of monitored symbols."""
        return self._symbols.copy()

    def get_exchange_status(self) -> Dict[str, dict]:
        """Get status of each exchange's rate feed."""
        now = datetime.now(timezone.utc)
        status = {}

        for name in self.exchanges:
            last_update = self._last_update.get(name)
            if last_update:
                seconds_ago = (now - last_update).total_seconds()
                status[name] = {
                    "connected": True,
                    "last_update": last_update.isoformat(),
                    "seconds_ago": seconds_ago,
                    "stale": seconds_ago > 120,  # Consider stale after 2 minutes
                }
            else:
                status[name] = {
                    "connected": False,
                    "last_update": None,
                    "seconds_ago": None,
                    "stale": True,
                }

        return status
