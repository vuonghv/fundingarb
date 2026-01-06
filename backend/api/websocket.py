"""
WebSocket manager for real-time updates.

Handles WebSocket connections and broadcasts events to all connected clients.
"""

import asyncio
import json
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Set

from fastapi import WebSocket

from ..database.connection import get_session
from ..database.repository import PositionRepository
from ..utils.logging import get_logger

logger = get_logger(__name__)


class WebSocketManager:
    """
    Manages WebSocket connections and broadcasts.

    Features:
    - Connection management
    - Broadcast to all clients
    - Event filtering per client
    - Heartbeat for connection health
    """

    def __init__(self):
        self._connections: Set[WebSocket] = set()
        self._heartbeat_interval = 30  # seconds
        self._heartbeat_task: Optional[asyncio.Task] = None

    @property
    def connection_count(self) -> int:
        """Get number of active connections."""
        return len(self._connections)

    async def connect(self, websocket: WebSocket) -> None:
        """
        Accept a new WebSocket connection.

        Args:
            websocket: WebSocket connection to add
        """
        await websocket.accept()
        self._connections.add(websocket)
        logger.info("websocket_connected", connections=self.connection_count)

        # Start heartbeat if not running
        if self._heartbeat_task is None or self._heartbeat_task.done():
            self._heartbeat_task = asyncio.create_task(self._heartbeat_loop())

    def disconnect(self, websocket: WebSocket) -> None:
        """
        Remove a WebSocket connection.

        Args:
            websocket: WebSocket connection to remove
        """
        self._connections.discard(websocket)
        logger.info("websocket_disconnected", connections=self.connection_count)

    async def broadcast(self, event_type: str, data: Dict[str, Any]) -> None:
        """
        Broadcast an event to all connected clients.

        Args:
            event_type: Event type identifier
            data: Event data payload
        """
        if not self._connections:
            return

        message = json.dumps({
            "type": event_type,
            "data": data,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })

        # Send to all connections, removing dead ones
        dead_connections = set()

        for connection in self._connections:
            try:
                await connection.send_text(message)
            except Exception:
                dead_connections.add(connection)

        # Remove dead connections
        for conn in dead_connections:
            self._connections.discard(conn)

    async def send_to(self, websocket: WebSocket, event_type: str, data: Dict[str, Any]) -> None:
        """
        Send an event to a specific client.

        Args:
            websocket: Target WebSocket connection
            event_type: Event type identifier
            data: Event data payload
        """
        message = json.dumps({
            "type": event_type,
            "data": data,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })

        try:
            await websocket.send_text(message)
        except Exception as e:
            logger.warning("websocket_send_failed", error=str(e))
            self._connections.discard(websocket)

    async def _heartbeat_loop(self) -> None:
        """Send periodic heartbeats to keep connections alive."""
        while self._connections:
            await asyncio.sleep(self._heartbeat_interval)
            await self.broadcast("HEARTBEAT", {"timestamp": datetime.now(timezone.utc).isoformat()})

    async def send_initial_state(self, websocket: WebSocket) -> None:
        """
        Send current state to a newly connected client.

        Sends ENGINE_STATUS, STATS, and FUNDING_RATE_UPDATE messages
        so the client has immediate data without waiting for broadcasts.
        """
        # Access app state via the websocket object (no imports needed)
        app = getattr(websocket, 'app', None)
        if not app:
            logger.debug("websocket_no_app_reference")
            return

        coordinator = getattr(app.state, 'coordinator', None)

        # 1. Send engine status
        if coordinator:
            try:
                status = coordinator.get_status()
                await self.send_to(websocket, "ENGINE_STATUS", {
                    "status": status.state.value,
                    "connected_exchanges": status.connected_exchanges,
                    "monitored_symbols": status.monitored_symbols,
                    "open_positions": status.open_positions,
                    "simulation_mode": status.simulation_mode,
                    "kill_switch_active": status.kill_switch_active,
                    "last_scan": status.last_scan_time.isoformat() if status.last_scan_time else None,
                    "error": status.error_message,
                })
            except Exception as e:
                logger.warning("initial_state_engine_status_failed", error=str(e))

        # 2. Send trading stats
        try:
            async with get_session() as session:
                repo = PositionRepository(session)
                open_count = await repo.count_open_positions()
                total_pnl = await repo.get_total_pnl()
                total_funding = await repo.get_total_funding()

                await self.send_to(websocket, "STATS", {
                    "open_positions": open_count,
                    "total_realized_pnl": float(total_pnl),
                    "total_funding_collected": float(total_funding),
                })
        except Exception as e:
            logger.warning("initial_state_stats_failed", error=str(e))

        # 3. Send cached funding rates
        if coordinator and hasattr(coordinator, 'scanner') and coordinator.scanner:
            try:
                rates = coordinator.scanner.get_rates()
                for exchange, exchange_rates in rates.items():
                    for symbol, rate in exchange_rates.items():
                        await self.send_to(websocket, "FUNDING_RATE_UPDATE", {
                            "exchange": exchange,
                            "pair": rate.symbol,
                            "rate": float(rate.rate),
                            "predicted": float(rate.predicted_rate) if rate.predicted_rate else None,
                            "next_funding_time": rate.next_funding_time.isoformat() if rate.next_funding_time else None,
                            "interval_hours": rate.interval_hours,
                            "mark_price": str(rate.mark_price) if rate.mark_price else None,
                            "index_price": str(rate.index_price) if rate.index_price else None,
                        })
            except Exception as e:
                logger.warning("initial_state_funding_rates_failed", error=str(e))

        logger.debug("initial_state_sent", websocket_id=id(websocket))

    # ==================== Event Helper Methods ====================

    async def send_position_update(
        self,
        position_id: str,
        status: str,
        unrealized_pnl: Optional[float],
        funding_collected: float,
    ) -> None:
        """Send position update event."""
        await self.broadcast("POSITION_UPDATE", {
            "position_id": position_id,
            "status": status,
            "unrealized_pnl": unrealized_pnl,
            "funding_collected": funding_collected,
        })

    async def send_funding_rate_update(
        self,
        exchange: str,
        pair: str,
        rate: float,
        predicted: Optional[float],
        next_funding_time: datetime,
        interval_hours: int = 8,
        mark_price: Optional[str] = None,
        index_price: Optional[str] = None,
    ) -> None:
        """Send funding rate update event."""
        await self.broadcast("FUNDING_RATE_UPDATE", {
            "exchange": exchange,
            "pair": pair,
            "rate": rate,
            "predicted": predicted,
            "next_funding_time": next_funding_time.isoformat(),
            "interval_hours": interval_hours,
            "mark_price": mark_price,
            "index_price": index_price,
        })

    async def send_price_update(
        self,
        exchange: str,
        pair: str,
        mark_price: str,
        index_price: str,
    ) -> None:
        """Send price update event for real-time price data."""
        await self.broadcast("PRICE_UPDATE", {
            "exchange": exchange,
            "pair": pair,
            "mark_price": mark_price,
            "index_price": index_price,
        })

    async def send_trade_executed(
        self,
        position_id: str,
        exchange: str,
        side: str,
        price: float,
        size: float,
        fee: float,
    ) -> None:
        """Send trade executed event."""
        await self.broadcast("TRADE_EXECUTED", {
            "position_id": position_id,
            "exchange": exchange,
            "side": side,
            "price": price,
            "size": size,
            "fee": fee,
        })

    async def send_engine_status(
        self,
        status: str,
        connected_exchanges: List[str],
        last_scan: Optional[datetime],
        error: Optional[str],
    ) -> None:
        """Send engine status event."""
        await self.broadcast("ENGINE_STATUS", {
            "status": status,
            "connected_exchanges": connected_exchanges,
            "last_scan": last_scan.isoformat() if last_scan else None,
            "error": error,
        })

    async def send_alert(
        self,
        severity: str,
        title: str,
        message: str,
    ) -> None:
        """Send alert event."""
        await self.broadcast("ALERT", {
            "severity": severity,
            "title": title,
            "message": message,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })

    async def send_opportunity(
        self,
        symbol: str,
        long_exchange: str,
        short_exchange: str,
        spread: float,
        expected_profit: float,
    ) -> None:
        """Send new opportunity event."""
        await self.broadcast("OPPORTUNITY", {
            "symbol": symbol,
            "long_exchange": long_exchange,
            "short_exchange": short_exchange,
            "spread": spread,
            "expected_profit": expected_profit,
        })


# Global WebSocket manager instance
ws_manager = WebSocketManager()
