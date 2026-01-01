"""
Trading engine coordinator.

Orchestrates all engine components and manages the trading loop.
"""

import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from enum import Enum
from typing import Dict, List, Optional, Callable

from sqlalchemy.ext.asyncio import AsyncSession

from ..config.schema import TradingConfig
from ..database.connection import get_session
from ..exchanges.base import ExchangeAdapter
from ..exchanges.types import FundingRate
from ..utils.logging import get_logger
from .scanner import FundingRateScanner
from .detector import ArbitrageDetector, ArbitrageOpportunity
from .executor import ExecutionEngine, ExecutionResult
from .position_manager import PositionManager
from .risk_manager import RiskManager

logger = get_logger(__name__)

# WebSocket manager - imported lazily to avoid circular imports
_ws_manager = None


def get_ws_manager():
    """Get WebSocket manager instance (lazy import to avoid circular imports)."""
    global _ws_manager
    if _ws_manager is None:
        from ..api.websocket import ws_manager
        _ws_manager = ws_manager
    return _ws_manager


class EngineState(Enum):
    """Trading engine state."""
    STOPPED = "STOPPED"
    STARTING = "STARTING"
    RUNNING = "RUNNING"
    STOPPING = "STOPPING"
    ERROR = "ERROR"


@dataclass
class EngineStatus:
    """Current engine status."""
    state: EngineState
    simulation_mode: bool
    connected_exchanges: List[str]
    monitored_symbols: List[str]
    open_positions: int
    last_scan_time: Optional[datetime]
    last_opportunity_time: Optional[datetime]
    pending_orders: int
    kill_switch_active: bool
    error_message: Optional[str] = None


class TradingCoordinator:
    """
    Main trading engine coordinator.

    Orchestrates:
    - Funding rate scanning
    - Opportunity detection
    - Position execution
    - Risk management
    - Position lifecycle
    """

    def __init__(
        self,
        config: TradingConfig,
        exchanges: Dict[str, ExchangeAdapter],
        alert_callback: Optional[Callable] = None,
    ):
        """
        Initialize trading coordinator.

        Args:
            config: Trading configuration
            exchanges: Connected exchange adapters
            alert_callback: Optional callback for sending alerts
        """
        self.config = config
        self.exchanges = exchanges
        self._alert_callback = alert_callback

        # State
        self._state = EngineState.STOPPED
        self._error_message: Optional[str] = None
        self._last_scan_time: Optional[datetime] = None
        self._last_opportunity_time: Optional[datetime] = None

        # Components
        self.scanner = FundingRateScanner(exchanges)
        self.detector = ArbitrageDetector(config)
        self.executor = ExecutionEngine(exchanges, config)
        self.risk_manager = RiskManager(config, exchanges)

        # Position manager created per-session
        self._position_manager: Optional[PositionManager] = None

        # Event callbacks
        self._on_position_opened: List[Callable] = []
        self._on_position_closed: List[Callable] = []
        self._on_funding_received: List[Callable] = []

        # Background tasks
        self._main_task: Optional[asyncio.Task] = None
        self._funding_task: Optional[asyncio.Task] = None

        # Set alert callback on risk manager
        if alert_callback:
            self.risk_manager.set_alert_callback(alert_callback)

    @property
    def state(self) -> EngineState:
        """Get current engine state."""
        return self._state

    @property
    def is_running(self) -> bool:
        """Check if engine is running."""
        return self._state == EngineState.RUNNING

    async def start(self) -> None:
        """Start the trading engine."""
        if self._state != EngineState.STOPPED:
            logger.warning("engine_not_stopped", current_state=self._state.value)
            return

        logger.info("engine_starting")
        self._state = EngineState.STARTING

        try:
            # Start funding rate scanner
            await self.scanner.start(self.config.symbols)

            # Register callback for opportunity detection
            self.scanner.on_update(self._on_rates_update)

            # Start main trading loop
            self._main_task = asyncio.create_task(self._main_loop())

            # Start funding payment tracker
            self._funding_task = asyncio.create_task(self._funding_loop())

            self._state = EngineState.RUNNING
            logger.info("engine_started")

            # Broadcast engine status via WebSocket
            await self._broadcast_engine_status()

            # Send notification
            await self._send_alert(
                "INFO",
                "Engine Started",
                f"Mode: {'SIMULATION' if self.config.simulation_mode else 'LIVE'}\n"
                f"Symbols: {', '.join(self.config.symbols)}\n"
                f"Exchanges: {', '.join(self.exchanges.keys())}",
            )

        except Exception as e:
            logger.exception("engine_start_failed", error=str(e))
            self._state = EngineState.ERROR
            self._error_message = str(e)
            raise

    async def stop(self) -> None:
        """Stop the trading engine gracefully."""
        if self._state not in (EngineState.RUNNING, EngineState.STARTING):
            return

        logger.info("engine_stopping")
        self._state = EngineState.STOPPING

        # Cancel background tasks
        if self._main_task:
            self._main_task.cancel()
            try:
                await self._main_task
            except asyncio.CancelledError:
                pass

        if self._funding_task:
            self._funding_task.cancel()
            try:
                await self._funding_task
            except asyncio.CancelledError:
                pass

        # Stop scanner
        await self.scanner.stop()

        self._state = EngineState.STOPPED
        logger.info("engine_stopped")

        # Broadcast engine status via WebSocket
        await self._broadcast_engine_status()

    async def _main_loop(self) -> None:
        """Main trading loop - processes opportunities."""
        logger.info("main_loop_started")

        while self._state == EngineState.RUNNING:
            try:
                await asyncio.sleep(1)  # Check every second

                # Skip if kill switch is active
                if self.risk_manager.is_kill_switch_active:
                    continue

                # Process any pending opportunities
                # (Opportunities are detected in _on_rates_update callback)

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.exception("main_loop_error", error=str(e))
                await asyncio.sleep(5)

        logger.info("main_loop_stopped")

    async def _funding_loop(self) -> None:
        """Track and record funding payments."""
        logger.info("funding_loop_started")

        while self._state == EngineState.RUNNING:
            try:
                # Check every 5 minutes for funding payments
                await asyncio.sleep(300)

                async with get_session() as session:
                    position_manager = PositionManager(session, self.exchanges)
                    positions = await position_manager.get_open_positions()

                    for position in positions:
                        await self._check_funding_payment(position_manager, position)

                    # Also check for liquidations
                    await self._check_liquidations(position_manager, positions)

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.exception("funding_loop_error", error=str(e))
                await asyncio.sleep(60)

        logger.info("funding_loop_stopped")

    async def _check_funding_payment(self, position_manager, position) -> None:
        """Check and record funding payment for a position."""
        try:
            # Get current funding rates for this position's pair
            long_exchange = self.exchanges.get(position.long_exchange)
            short_exchange = self.exchanges.get(position.short_exchange)

            if not long_exchange or not short_exchange:
                return

            # Check if we just passed a funding time
            # This is simplified - in production you'd track the exact funding time
            from datetime import timedelta

            now = datetime.now(timezone.utc)

            # Try to get funding rate and check if payment is due
            try:
                long_rates = await long_exchange.get_funding_rates([position.pair])
                short_rates = await short_exchange.get_funding_rates([position.pair])

                long_rate = long_rates.get(position.pair)
                short_rate = short_rates.get(position.pair)

                if long_rate and short_rate:
                    # Check if next funding time is in the past (just paid)
                    # or if we're within a few seconds of it
                    for rate, exch_name, size, side in [
                        (long_rate, position.long_exchange, position.long_size, "long"),
                        (short_rate, position.short_exchange, position.short_size, "short"),
                    ]:
                        if rate.next_funding_time:
                            # Calculate time since last funding (assuming 8h intervals)
                            interval_seconds = rate.interval_hours * 3600
                            last_funding = rate.next_funding_time - timedelta(seconds=interval_seconds)

                            # If last funding was within the last 5 minutes, record it
                            if (now - last_funding).total_seconds() < 300:
                                payment = float(rate.rate) * float(size or 0)
                                # Long pays when rate is positive, short receives
                                if side == "short":
                                    payment = -payment  # Short receives when rate is positive

                                if abs(payment) > 0.001:  # Only record meaningful amounts
                                    await position_manager.record_funding_payment(
                                        position_id=position.id,
                                        exchange=exch_name,
                                        rate=float(rate.rate),
                                        payment_usd=payment,
                                    )

                                    # Notify callbacks
                                    for callback in self._on_funding_received:
                                        try:
                                            await callback(position, exch_name, payment)
                                        except Exception as e:
                                            logger.error("funding_callback_error", error=str(e))

                                    # Broadcast position update with new funding
                                    await self._broadcast_position_update(
                                        position_id=position.id,
                                        status="OPEN",
                                        unrealized_pnl=None,
                                        funding_collected=float(position.funding_collected) + payment,
                                    )

                                    logger.info(
                                        "funding_payment_recorded",
                                        position_id=position.id,
                                        exchange=exch_name,
                                        payment=payment,
                                    )

            except Exception as e:
                logger.debug("funding_check_error", position_id=position.id, error=str(e))

        except Exception as e:
            logger.error("funding_payment_check_failed", error=str(e))

    async def _check_liquidations(self, position_manager, positions) -> None:
        """Check for liquidations on open positions."""
        for position in positions:
            try:
                is_liquidated = await self.risk_manager.check_for_liquidation(
                    position.pair,
                    position.long_exchange,
                    position.short_exchange,
                )
                if is_liquidated:
                    logger.warning(
                        "liquidation_detected",
                        position_id=position.id,
                        pair=position.pair,
                    )
                    await self._send_alert(
                        "ERROR",
                        "Liquidation Detected",
                        f"Position {position.id} ({position.pair}) may have been liquidated!",
                    )
                    await self._broadcast_alert(
                        severity="ERROR",
                        title="Liquidation Detected",
                        message=f"Position {position.pair} may have been liquidated!",
                    )
            except Exception as e:
                logger.debug("liquidation_check_error", position_id=position.id, error=str(e))

    def _on_rates_update(self, rates: Dict[str, Dict[str, FundingRate]]) -> None:
        """Handle funding rate updates from scanner."""
        self._last_scan_time = datetime.now(timezone.utc)

        # Broadcast funding rates via WebSocket
        asyncio.create_task(self._broadcast_rates(rates))

        # Run opportunity check in background
        asyncio.create_task(self._process_opportunities(rates))

    async def _broadcast_rates(self, rates: Dict[str, Dict[str, FundingRate]]) -> None:
        """Broadcast all funding rates via WebSocket."""
        for exchange, exchange_rates in rates.items():
            for symbol, rate in exchange_rates.items():
                await self._broadcast_funding_rate(exchange, rate)

    async def _process_opportunities(
        self,
        rates: Dict[str, Dict[str, FundingRate]],
    ) -> None:
        """Process funding rate updates and execute opportunities."""
        if not self.risk_manager.is_trading_enabled:
            return

        try:
            async with get_session() as session:
                position_manager = PositionManager(session, self.exchanges)

                # Get currently open pairs
                open_positions = await position_manager.get_open_positions()
                open_pairs = [p.pair for p in open_positions]

                # Find best opportunity
                opportunity = self.detector.find_best_opportunity(
                    rates,
                    self.config.max_position_per_pair_usd,
                    excluded_pairs=open_pairs,
                )

                if not opportunity:
                    return

                self._last_opportunity_time = datetime.now(timezone.utc)

                # Check if we can open this position
                can_open, reason = self.risk_manager.can_open_position(
                    opportunity.symbol,
                    self.config.max_position_per_pair_usd,
                )

                if not can_open:
                    logger.debug("cannot_open_position", reason=reason)
                    return

                # Check timing - only enter if we have enough time before funding
                min_time = self.config.entry_buffer_minutes * 60  # Convert to seconds
                if opportunity.seconds_to_funding < min_time:
                    logger.debug(
                        "too_close_to_funding",
                        seconds_remaining=opportunity.seconds_to_funding,
                        min_required=min_time,
                    )
                    return

                # Execute the opportunity
                await self._execute_opportunity(position_manager, opportunity)

        except Exception as e:
            logger.exception("process_opportunities_error", error=str(e))

    async def _execute_opportunity(
        self,
        position_manager: PositionManager,
        opportunity: ArbitrageOpportunity,
    ) -> None:
        """Execute an arbitrage opportunity."""
        logger.info(
            "executing_opportunity",
            symbol=opportunity.symbol,
            spread=float(opportunity.spread),
            long_exchange=opportunity.long_exchange,
            short_exchange=opportunity.short_exchange,
        )

        size_usd = self.config.max_position_per_pair_usd

        # Execute entry
        result = await self.executor.execute_entry(opportunity, size_usd)

        if result.success:
            # Create position record
            position = await position_manager.create_position(
                opportunity, result, size_usd
            )

            logger.info(
                "position_opened",
                position_id=position.id,
                symbol=opportunity.symbol,
            )

            # Notify callbacks
            for callback in self._on_position_opened:
                try:
                    await callback(position, opportunity)
                except Exception as e:
                    logger.error("position_opened_callback_error", error=str(e))

            # Broadcast via WebSocket
            await self._broadcast_position_update(
                position_id=position.id,
                status="OPEN",
                unrealized_pnl=0.0,
                funding_collected=0.0,
            )

            # Broadcast opportunity taken
            await self._broadcast_opportunity(
                symbol=opportunity.symbol,
                long_exchange=opportunity.long_exchange,
                short_exchange=opportunity.short_exchange,
                spread=float(opportunity.spread),
                expected_profit=float(opportunity.expected_daily_profit_usd),
            )

            # Send alert
            await self._send_alert(
                "INFO",
                "Position Opened",
                f"Pair: {opportunity.symbol}\n"
                f"Long: {opportunity.long_exchange} @ ${result.long_order.average_price if result.long_order else 'N/A'}\n"
                f"Short: {opportunity.short_exchange} @ ${result.short_order.average_price if result.short_order else 'N/A'}\n"
                f"Size: ${size_usd:,.0f}\n"
                f"Spread: {float(opportunity.spread) * 100:.4f}%",
            )

            # Broadcast trade executed events
            if result.long_order:
                await self._broadcast_trade_executed(
                    position_id=position.id,
                    exchange=opportunity.long_exchange,
                    side="BUY",
                    price=float(result.long_order.average_price) if result.long_order.average_price else 0,
                    size=float(result.long_order.filled_size),
                    fee=float(result.long_order.fee),
                )
            if result.short_order:
                await self._broadcast_trade_executed(
                    position_id=position.id,
                    exchange=opportunity.short_exchange,
                    side="SELL",
                    price=float(result.short_order.average_price) if result.short_order.average_price else 0,
                    size=float(result.short_order.filled_size),
                    fee=float(result.short_order.fee),
                )
        else:
            logger.warning(
                "execution_failed",
                symbol=opportunity.symbol,
                error=result.error_message,
            )

            await self._send_alert(
                "WARNING",
                "Execution Failed",
                f"Pair: {opportunity.symbol}\n"
                f"Error: {result.error_message}",
            )

    async def close_position(self, position_id: str, reason: str = "manual") -> bool:
        """
        Manually close a position.

        Args:
            position_id: Position ID to close
            reason: Reason for closing (manual, take_profit, stop_loss, kill_switch)

        Returns:
            True if closed successfully
        """
        async with get_session() as session:
            position_manager = PositionManager(session, self.exchanges)

            position = await position_manager.get_position(position_id)
            if not position:
                logger.warning("position_not_found", position_id=position_id)
                return False

            if not position.is_open:
                logger.warning("position_already_closed", position_id=position_id)
                return False

            # Execute exit
            result = await self.executor.execute_exit(
                position.pair,
                position.long_exchange,
                position.short_exchange,
                position.long_size or Decimal("0"),
                position.short_size or Decimal("0"),
            )

            if result.success:
                closed_position = await position_manager.close_position(position_id, result)
                logger.info("position_closed", position_id=position_id, reason=reason)

                # Notify callbacks
                for callback in self._on_position_closed:
                    try:
                        await callback(closed_position, reason)
                    except Exception as e:
                        logger.error("position_closed_callback_error", error=str(e))

                # Broadcast via WebSocket
                await self._broadcast_position_update(
                    position_id=position_id,
                    status="CLOSED",
                    unrealized_pnl=None,
                    funding_collected=float(closed_position.funding_collected) if closed_position else 0.0,
                )

                # Broadcast trade executed events for close
                if result.long_order:
                    await self._broadcast_trade_executed(
                        position_id=position_id,
                        exchange=position.long_exchange,
                        side="SELL",
                        price=float(result.long_order.average_price) if result.long_order.average_price else 0,
                        size=float(result.long_order.filled_size),
                        fee=float(result.long_order.fee),
                    )
                if result.short_order:
                    await self._broadcast_trade_executed(
                        position_id=position_id,
                        exchange=position.short_exchange,
                        side="BUY",
                        price=float(result.short_order.average_price) if result.short_order.average_price else 0,
                        size=float(result.short_order.filled_size),
                        fee=float(result.short_order.fee),
                    )

                # Send alert
                realized_pnl = float(closed_position.realized_pnl) if closed_position and closed_position.realized_pnl else 0
                await self._send_alert(
                    "INFO",
                    "Position Closed",
                    f"Pair: {position.pair}\n"
                    f"Reason: {reason}\n"
                    f"Realized PnL: ${realized_pnl:,.2f}",
                )

                return True
            else:
                logger.error(
                    "position_close_failed",
                    position_id=position_id,
                    error=result.error_message,
                )
                return False

    async def activate_kill_switch(self, reason: str = "Manual") -> None:
        """Activate the kill switch."""
        await self.risk_manager.activate_kill_switch(reason)

    def deactivate_kill_switch(self) -> None:
        """Deactivate the kill switch."""
        self.risk_manager.deactivate_kill_switch()

    def get_status(self) -> EngineStatus:
        """Get current engine status."""
        return EngineStatus(
            state=self._state,
            simulation_mode=self.config.simulation_mode,
            connected_exchanges=[
                name for name, exch in self.exchanges.items()
                if exch.is_connected
            ],
            monitored_symbols=list(self.scanner.monitored_symbols),
            open_positions=0,  # Would need to query DB
            last_scan_time=self._last_scan_time,
            last_opportunity_time=self._last_opportunity_time,
            pending_orders=self.executor.pending_orders_count,
            kill_switch_active=self.risk_manager.is_kill_switch_active,
            error_message=self._error_message,
        )

    async def _send_alert(self, severity: str, title: str, message: str) -> None:
        """Send alert via callback."""
        if self._alert_callback:
            try:
                await self._alert_callback(severity, title, message)
            except Exception as e:
                logger.error("alert_send_failed", error=str(e))

    def on_position_opened(self, callback: Callable) -> None:
        """Register callback for position opened events."""
        self._on_position_opened.append(callback)

    def on_position_closed(self, callback: Callable) -> None:
        """Register callback for position closed events."""
        self._on_position_closed.append(callback)

    async def reconcile_state(self) -> List[str]:
        """Reconcile local state with exchanges."""
        async with get_session() as session:
            position_manager = PositionManager(session, self.exchanges)
            return await position_manager.reconcile_with_exchanges()

    async def save_checkpoint(self) -> None:
        """Save engine state checkpoint."""
        # State is persisted in database, so just log
        logger.info("checkpoint_saved", state=self._state.value)

    # ==================== WebSocket Broadcast Methods ====================

    async def _broadcast_position_update(
        self,
        position_id: str,
        status: str,
        unrealized_pnl: Optional[float],
        funding_collected: float,
    ) -> None:
        """Broadcast position update via WebSocket."""
        try:
            ws = get_ws_manager()
            await ws.send_position_update(
                position_id=position_id,
                status=status,
                unrealized_pnl=unrealized_pnl,
                funding_collected=funding_collected,
            )
        except Exception as e:
            logger.debug("websocket_broadcast_failed", error=str(e))

    async def _broadcast_trade_executed(
        self,
        position_id: str,
        exchange: str,
        side: str,
        price: float,
        size: float,
        fee: float,
    ) -> None:
        """Broadcast trade executed via WebSocket."""
        try:
            ws = get_ws_manager()
            await ws.send_trade_executed(
                position_id=position_id,
                exchange=exchange,
                side=side,
                price=price,
                size=size,
                fee=fee,
            )
        except Exception as e:
            logger.debug("websocket_broadcast_failed", error=str(e))

    async def _broadcast_opportunity(
        self,
        symbol: str,
        long_exchange: str,
        short_exchange: str,
        spread: float,
        expected_profit: float,
    ) -> None:
        """Broadcast opportunity via WebSocket."""
        try:
            ws = get_ws_manager()
            await ws.send_opportunity(
                symbol=symbol,
                long_exchange=long_exchange,
                short_exchange=short_exchange,
                spread=spread,
                expected_profit=expected_profit,
            )
        except Exception as e:
            logger.debug("websocket_broadcast_failed", error=str(e))

    async def _broadcast_engine_status(self) -> None:
        """Broadcast current engine status via WebSocket."""
        try:
            ws = get_ws_manager()
            await ws.send_engine_status(
                status=self._state.value,
                connected_exchanges=[
                    name for name, exch in self.exchanges.items()
                    if exch.is_connected
                ],
                last_scan=self._last_scan_time,
                error=self._error_message,
            )
        except Exception as e:
            logger.debug("websocket_broadcast_failed", error=str(e))

    async def _broadcast_funding_rate(self, exchange: str, rate: FundingRate) -> None:
        """Broadcast funding rate update via WebSocket."""
        try:
            ws = get_ws_manager()
            await ws.send_funding_rate_update(
                exchange=exchange,
                pair=rate.symbol,
                rate=float(rate.rate),
                predicted=float(rate.predicted_rate) if rate.predicted_rate else None,
                next_funding_time=rate.next_funding_time,
                interval_hours=rate.interval_hours,
                mark_price=str(rate.mark_price) if rate.mark_price else None,
                index_price=str(rate.index_price) if rate.index_price else None,
            )
        except Exception as e:
            logger.debug("websocket_broadcast_failed", error=str(e))

    async def _broadcast_alert(self, severity: str, title: str, message: str) -> None:
        """Broadcast alert via WebSocket."""
        try:
            ws = get_ws_manager()
            await ws.send_alert(
                severity=severity,
                title=title,
                message=message,
            )
        except Exception as e:
            logger.debug("websocket_broadcast_failed", error=str(e))
