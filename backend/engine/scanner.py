"""
Funding rate scanner.

Monitors funding rates across all configured exchanges
and triggers callbacks when rates update.

Simplified design:
- Single polling loop fetches from all exchanges in parallel
- Async callback is awaited directly (no fire-and-forget)
- Easy to debug and maintain for low-frequency workloads
"""

import asyncio
from datetime import datetime, timezone
from typing import Awaitable, Callable, Dict, List, Optional, Set

from ..exchanges.base import ExchangeAdapter
from ..exchanges.types import FundingRate
from ..utils.logging import get_logger

logger = get_logger(__name__)

# Type alias for the async callback
RatesCallback = Callable[[Dict[str, Dict[str, FundingRate]]], Awaitable[None]]


class FundingRateScanner:
    """
    Scans funding rates across exchanges.

    Features:
    - Single polling loop for all exchanges
    - Parallel fetching with asyncio.gather
    - Async callback for processing (no fire-and-forget)
    - Caches latest rates for quick access
    """

    # Polling interval in seconds
    POLL_INTERVAL = 30

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

        # Async callback for rate updates
        self._on_rates_callback: Optional[RatesCallback] = None

        # Track which symbols we're monitoring
        self._symbols: Set[str] = set()

        # Last update timestamp per exchange
        self._last_update: Dict[str, datetime] = {}

        # Background polling task
        self._poll_task: Optional[asyncio.Task] = None

    async def start(
        self,
        symbols: List[str],
        on_rates_update: Optional[RatesCallback] = None,
    ) -> None:
        """
        Start scanning funding rates.

        Args:
            symbols: List of symbols to monitor
            on_rates_update: Async callback when rates are updated
        """
        if self._running:
            logger.warning("scanner_already_running")
            return

        self._symbols = set(symbols)
        self._on_rates_callback = on_rates_update
        self._running = True

        logger.info(
            "scanner_starting",
            symbols=list(symbols),
            exchanges=list(self.exchanges.keys()),
            poll_interval=self.POLL_INTERVAL,
        )

        # Initial fetch to populate cache
        await self._fetch_all_rates()

        # Notify callback with initial rates
        if self._on_rates_callback and self._rates:
            try:
                await self._on_rates_callback(self._rates)
            except Exception as e:
                logger.error("initial_callback_error", error=str(e))

        # Start polling loop
        self._poll_task = asyncio.create_task(self._poll_loop())
        logger.info("scanner_started")

    async def stop(self) -> None:
        """Stop scanning and clean up."""
        self._running = False

        # Cancel polling task
        if self._poll_task:
            self._poll_task.cancel()
            try:
                await self._poll_task
            except asyncio.CancelledError:
                pass
            self._poll_task = None

        self._on_rates_callback = None
        logger.info("scanner_stopped")

    async def _poll_loop(self) -> None:
        """
        Main polling loop.

        Fetches rates from all exchanges in parallel, then awaits the callback.
        Sequential processing is intentional - for 5 symbols × 4 exchanges,
        the entire cycle takes ~400ms, leaving 29.6s of idle time.
        """
        logger.info("poll_loop_started")

        while self._running:
            try:
                # Wait for next poll interval
                await asyncio.sleep(self.POLL_INTERVAL)

                if not self._running:
                    break

                # Fetch from all exchanges in parallel
                await self._fetch_all_rates()

                # Await the callback (not fire-and-forget)
                if self._on_rates_callback and self._rates:
                    await self._on_rates_callback(self._rates)

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.exception("poll_loop_error", error=str(e))
                # Continue polling despite errors
                await asyncio.sleep(5)

        logger.info("poll_loop_stopped")

    async def _fetch_all_rates(self) -> None:
        """
        Fetch current rates from all exchanges in parallel.

        Uses asyncio.gather for concurrent HTTP requests, reducing
        total latency from ~1200ms (sequential) to ~300ms (parallel).
        """
        async def fetch_exchange(name: str, exchange: ExchangeAdapter) -> tuple:
            """Fetch rates from a single exchange."""
            try:
                rates = await exchange.get_funding_rates(list(self._symbols))
                return (name, rates, None)
            except Exception as e:
                return (name, None, e)

        # Fetch all exchanges in parallel
        tasks = [
            fetch_exchange(name, exchange)
            for name, exchange in self.exchanges.items()
        ]
        results = await asyncio.gather(*tasks)

        # Process results
        for name, rates, error in results:
            if error:
                logger.error("fetch_rates_failed", exchange=name, error=str(error))
                continue

            if rates:
                if name not in self._rates:
                    self._rates[name] = {}

                for symbol, rate in rates.items():
                    self._rates[name][symbol] = rate

                self._last_update[name] = datetime.now(timezone.utc)

                logger.debug(
                    "rates_fetched",
                    exchange=name,
                    count=len(rates),
                )

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
