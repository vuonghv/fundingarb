"""
Risk management module.

Handles position limits, kill switch, liquidation detection,
and other risk controls.
"""

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Dict, List, Optional

from ..config.schema import TradingConfig
from ..exchanges.base import ExchangeAdapter
from ..exchanges.types import ExchangePosition, PositionSide
from ..utils.logging import get_logger

logger = get_logger(__name__)


class RiskManager:
    """
    Manages risk controls for the trading engine.

    Features:
    - Position size limits per pair
    - Kill switch (close all, halt)
    - Liquidation detection and handling
    - Pair cooldown after incidents
    """

    def __init__(
        self,
        config: TradingConfig,
        exchanges: Dict[str, ExchangeAdapter],
    ):
        """
        Initialize risk manager.

        Args:
            config: Trading configuration
            exchanges: Dict of connected exchange adapters
        """
        self.config = config
        self.exchanges = exchanges

        # Kill switch state
        self._kill_switch_active = False
        self._kill_switch_activated_at: Optional[datetime] = None

        # Paused pairs with cooldown expiry
        self._paused_pairs: Dict[str, datetime] = {}

        # Last known positions for liquidation detection
        self._last_positions: Dict[str, Dict[str, ExchangePosition]] = {}

        # Alert callback
        self._alert_callback = None

    def set_alert_callback(self, callback) -> None:
        """Set callback for sending alerts."""
        self._alert_callback = callback

    async def _send_alert(self, severity: str, title: str, message: str) -> None:
        """Send alert via callback if set."""
        if self._alert_callback:
            try:
                await self._alert_callback(severity, title, message)
            except Exception as e:
                logger.error("alert_callback_failed", error=str(e))

    # ==================== Position Limits ====================

    def check_position_limit(self, symbol: str, size_usd: Decimal) -> bool:
        """
        Check if position size is within limits.

        Args:
            symbol: Trading pair
            size_usd: Proposed position size

        Returns:
            True if within limits
        """
        max_size = self.config.max_position_per_pair_usd
        if size_usd > max_size:
            logger.warning(
                "position_limit_exceeded",
                symbol=symbol,
                requested=float(size_usd),
                max_allowed=float(max_size),
            )
            return False
        return True

    # ==================== Pair Pausing ====================

    def is_pair_paused(self, symbol: str) -> bool:
        """
        Check if a pair is currently paused (in cooldown).

        Args:
            symbol: Trading pair

        Returns:
            True if pair is paused
        """
        if symbol not in self._paused_pairs:
            return False

        expiry = self._paused_pairs[symbol]
        if datetime.now(timezone.utc) >= expiry:
            # Cooldown expired
            del self._paused_pairs[symbol]
            logger.info("pair_cooldown_expired", symbol=symbol)
            return False

        return True

    def pause_pair(self, symbol: str, cooldown_hours: float = 1.0) -> None:
        """
        Pause a pair for a cooldown period.

        Args:
            symbol: Trading pair to pause
            cooldown_hours: Cooldown duration in hours
        """
        expiry = datetime.now(timezone.utc) + timedelta(hours=cooldown_hours)
        self._paused_pairs[symbol] = expiry
        logger.warning(
            "pair_paused",
            symbol=symbol,
            cooldown_hours=cooldown_hours,
            expires_at=expiry.isoformat(),
        )

    def get_paused_pairs(self) -> Dict[str, datetime]:
        """Get all paused pairs with expiry times."""
        return self._paused_pairs.copy()

    # ==================== Kill Switch ====================

    @property
    def is_kill_switch_active(self) -> bool:
        """Check if kill switch is active."""
        return self._kill_switch_active

    @property
    def is_trading_enabled(self) -> bool:
        """Check if trading is enabled (kill switch not active)."""
        return not self._kill_switch_active

    async def activate_kill_switch(self, reason: str = "Manual activation") -> None:
        """
        Activate kill switch - close all positions and halt trading.

        Args:
            reason: Reason for activation
        """
        if self._kill_switch_active:
            logger.warning("kill_switch_already_active")
            return

        logger.critical("kill_switch_activated", reason=reason)
        self._kill_switch_active = True
        self._kill_switch_activated_at = datetime.now(timezone.utc)

        # Cancel all pending orders
        cancelled_count = await self._cancel_all_orders()
        logger.info("all_orders_cancelled", count=cancelled_count)

        # Close all positions
        closed_positions = await self._close_all_positions()
        logger.info("all_positions_closed", count=len(closed_positions))

        # Send critical alert
        await self._send_alert(
            "CRITICAL",
            "KILL SWITCH ACTIVATED",
            f"Reason: {reason}\n"
            f"Orders cancelled: {cancelled_count}\n"
            f"Positions closed: {len(closed_positions)}\n"
            f"Manual restart required to resume trading.",
        )

    def deactivate_kill_switch(self) -> None:
        """Deactivate kill switch (requires manual call)."""
        if not self._kill_switch_active:
            return

        logger.info("kill_switch_deactivated")
        self._kill_switch_active = False
        self._kill_switch_activated_at = None

    async def _cancel_all_orders(self) -> int:
        """Cancel all pending orders on all exchanges."""
        total_cancelled = 0

        for name, exchange in self.exchanges.items():
            try:
                count = await exchange.cancel_all_orders()
                total_cancelled += count
                logger.info("orders_cancelled", exchange=name, count=count)
            except Exception as e:
                logger.error("cancel_orders_failed", exchange=name, error=str(e))

        return total_cancelled

    async def _close_all_positions(self) -> List[str]:
        """Close all positions on all exchanges."""
        closed = []

        for name, exchange in self.exchanges.items():
            try:
                positions = await exchange.get_positions()
                for pos in positions:
                    if pos.size > 0:
                        try:
                            # Determine close side
                            from ..exchanges.types import Order, OrderSide, OrderType
                            close_side = OrderSide.SELL if pos.side == PositionSide.LONG else OrderSide.BUY

                            order = Order(
                                symbol=pos.symbol,
                                side=close_side,
                                order_type=OrderType.MARKET,
                                size=pos.size,
                                reduce_only=True,
                            )

                            await exchange.place_order(order)
                            closed.append(f"{name}:{pos.symbol}")
                            logger.info(
                                "position_force_closed",
                                exchange=name,
                                symbol=pos.symbol,
                                side=pos.side.value,
                            )
                        except Exception as e:
                            logger.error(
                                "force_close_failed",
                                exchange=name,
                                symbol=pos.symbol,
                                error=str(e),
                            )
            except Exception as e:
                logger.error("get_positions_failed", exchange=name, error=str(e))

        return closed

    # ==================== Liquidation Detection ====================

    async def check_for_liquidations(self) -> List[Dict]:
        """
        Check for any liquidated positions.

        Compares current positions with last known positions
        to detect unexpected closures.

        Returns:
            List of detected liquidations
        """
        liquidations = []

        for name, exchange in self.exchanges.items():
            try:
                current_positions = await exchange.get_positions()
                current_map = {p.symbol: p for p in current_positions}

                last_map = self._last_positions.get(name, {})

                # Check for positions that disappeared
                for symbol, last_pos in last_map.items():
                    if symbol not in current_map or current_map[symbol].size == 0:
                        # Position is gone - could be liquidation or manual close
                        if last_pos.liquidation_price:
                            # Check if mark price was near liquidation
                            liquidations.append({
                                "exchange": name,
                                "symbol": symbol,
                                "side": last_pos.side.value,
                                "size": float(last_pos.size),
                                "entry_price": float(last_pos.entry_price),
                                "liquidation_price": float(last_pos.liquidation_price),
                            })

                # Update last known positions
                self._last_positions[name] = current_map

            except Exception as e:
                logger.error("liquidation_check_failed", exchange=name, error=str(e))

        if liquidations:
            logger.warning("liquidations_detected", count=len(liquidations))

        return liquidations

    async def handle_liquidation(
        self,
        position_id: str,
        liquidated_exchange: str,
        surviving_exchange: str,
        surviving_symbol: str,
        surviving_side: str,
        surviving_size: Decimal,
    ) -> None:
        """
        Handle a liquidation event.

        Immediately closes the surviving leg and pauses the pair.

        Args:
            position_id: Local position ID
            liquidated_exchange: Exchange where liquidation occurred
            surviving_exchange: Exchange with remaining position
            surviving_symbol: Symbol of surviving position
            surviving_side: Side of surviving position (LONG/SHORT)
            surviving_size: Size of surviving position
        """
        logger.critical(
            "handling_liquidation",
            position_id=position_id,
            liquidated_exchange=liquidated_exchange,
            surviving_exchange=surviving_exchange,
        )

        # Close surviving leg immediately with market order
        try:
            from ..exchanges.types import Order, OrderSide, OrderType

            close_side = OrderSide.SELL if surviving_side == "LONG" else OrderSide.BUY

            order = Order(
                symbol=surviving_symbol,
                side=close_side,
                order_type=OrderType.MARKET,
                size=surviving_size,
                reduce_only=True,
            )

            result = await self.exchanges[surviving_exchange].place_order(order)
            logger.info(
                "surviving_leg_closed",
                exchange=surviving_exchange,
                order_id=result.order_id,
            )
        except Exception as e:
            logger.error("surviving_leg_close_failed", error=str(e))

        # Pause this pair
        self.pause_pair(surviving_symbol, cooldown_hours=1.0)

        # Send critical alert
        await self._send_alert(
            "CRITICAL",
            "LIQUIDATION DETECTED",
            f"Position: {position_id}\n"
            f"Liquidated on: {liquidated_exchange}\n"
            f"Surviving leg closed on: {surviving_exchange}\n"
            f"Pair {surviving_symbol} paused for 1 hour.",
        )

    # ==================== Risk Checks ====================

    def can_open_position(self, symbol: str, size_usd: Decimal) -> tuple[bool, str]:
        """
        Check if a new position can be opened.

        Args:
            symbol: Trading pair
            size_usd: Position size

        Returns:
            Tuple of (can_open, reason)
        """
        # Check kill switch
        if self._kill_switch_active:
            return False, "Kill switch is active"

        # Check pair pause
        if self.is_pair_paused(symbol):
            expiry = self._paused_pairs.get(symbol)
            return False, f"Pair is paused until {expiry.isoformat() if expiry else 'unknown'}"

        # Check position limit
        if not self.check_position_limit(symbol, size_usd):
            return False, f"Position size {size_usd} exceeds limit {self.config.max_position_per_pair_usd}"

        return True, "OK"

    def get_risk_status(self) -> dict:
        """Get current risk status summary."""
        return {
            "kill_switch_active": self._kill_switch_active,
            "kill_switch_activated_at": (
                self._kill_switch_activated_at.isoformat()
                if self._kill_switch_activated_at else None
            ),
            "trading_enabled": self.is_trading_enabled,
            "paused_pairs": {
                symbol: expiry.isoformat()
                for symbol, expiry in self._paused_pairs.items()
            },
            "max_position_per_pair": float(self.config.max_position_per_pair_usd),
        }
