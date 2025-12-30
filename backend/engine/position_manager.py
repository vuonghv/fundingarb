"""
Position lifecycle manager.

Handles position creation, tracking, funding payment recording,
and closing with P&L calculation.
"""

from datetime import datetime, timezone
from decimal import Decimal
from typing import Dict, List, Optional
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from ..database.models import (
    Position,
    Trade,
    FundingEvent,
    PositionStatus,
    OrderSide,
    OrderAction,
    OrderType as DbOrderType,
    TradeStatus,
)
from ..database.repository import PositionRepository, TradeRepository, FundingEventRepository
from ..exchanges.base import ExchangeAdapter
from ..exchanges.types import OrderResult, OrderSide as ExchangeOrderSide
from ..utils.logging import get_logger
from .detector import ArbitrageOpportunity
from .executor import ExecutionResult

logger = get_logger(__name__)


class PositionManager:
    """
    Manages the lifecycle of arbitrage positions.

    Responsibilities:
    - Create positions from successful executions
    - Track position state
    - Record funding payments
    - Close positions and calculate P&L
    - Reconcile with exchange state
    """

    def __init__(
        self,
        session: AsyncSession,
        exchanges: Dict[str, ExchangeAdapter],
    ):
        """
        Initialize position manager.

        Args:
            session: Database session
            exchanges: Dict of connected exchange adapters
        """
        self.session = session
        self.exchanges = exchanges

        # Repositories
        self.position_repo = PositionRepository(session)
        self.trade_repo = TradeRepository(session)
        self.funding_repo = FundingEventRepository(session)

    async def create_position(
        self,
        opportunity: ArbitrageOpportunity,
        execution: ExecutionResult,
        size_usd: Decimal,
    ) -> Position:
        """
        Create a new position from successful execution.

        Args:
            opportunity: The arbitrage opportunity that was executed
            execution: Execution result with order details
            size_usd: Position size in USD

        Returns:
            Created Position object
        """
        if not execution.success or not execution.long_order or not execution.short_order:
            raise ValueError("Cannot create position from failed execution")

        # Get leverage from exchanges (or use defaults)
        long_leverage = 5
        short_leverage = 5

        try:
            long_pos = await self.exchanges[opportunity.long_exchange].get_position(
                opportunity.symbol
            )
            if long_pos:
                long_leverage = long_pos.leverage
        except Exception:
            pass

        try:
            short_pos = await self.exchanges[opportunity.short_exchange].get_position(
                opportunity.symbol
            )
            if short_pos:
                short_leverage = short_pos.leverage
        except Exception:
            pass

        # Calculate total fees
        total_fees = (
            execution.long_order.fee +
            execution.short_order.fee
        )

        # Create position record
        position = Position(
            pair=opportunity.symbol,
            long_exchange=opportunity.long_exchange,
            short_exchange=opportunity.short_exchange,
            long_entry_price=execution.long_order.average_price or execution.long_order.price,
            short_entry_price=execution.short_order.average_price or execution.short_order.price,
            size_usd=size_usd,
            long_size=execution.long_order.filled_size,
            short_size=execution.short_order.filled_size,
            leverage_long=long_leverage,
            leverage_short=short_leverage,
            entry_funding_spread=opportunity.spread,
            total_fees=total_fees,
            status=PositionStatus.OPEN,
        )

        position = await self.position_repo.create(position)

        # Record trades
        await self._record_trade(
            position.id,
            execution.long_order,
            opportunity.long_exchange,
            OrderSide.LONG,
            OrderAction.OPEN,
        )

        await self._record_trade(
            position.id,
            execution.short_order,
            opportunity.short_exchange,
            OrderSide.SHORT,
            OrderAction.OPEN,
        )

        await self.session.commit()

        logger.info(
            "position_created",
            position_id=position.id,
            symbol=opportunity.symbol,
            size_usd=float(size_usd),
            spread=float(opportunity.spread),
        )

        return position

    async def close_position(
        self,
        position_id: str,
        execution: ExecutionResult,
    ) -> Position:
        """
        Close a position and calculate realized P&L.

        Args:
            position_id: Position ID to close
            execution: Execution result with close order details

        Returns:
            Updated Position object
        """
        position = await self.position_repo.get_by_id(position_id)
        if not position:
            raise ValueError(f"Position not found: {position_id}")

        if not position.is_open:
            raise ValueError(f"Position already closed: {position_id}")

        # Get close prices
        long_close_price = Decimal("0")
        short_close_price = Decimal("0")
        close_fees = Decimal("0")

        if execution.long_order:
            long_close_price = execution.long_order.average_price or execution.long_order.price or Decimal("0")
            close_fees += execution.long_order.fee

            await self._record_trade(
                position_id,
                execution.long_order,
                position.long_exchange,
                OrderSide.LONG,
                OrderAction.CLOSE,
            )

        if execution.short_order:
            short_close_price = execution.short_order.average_price or execution.short_order.price or Decimal("0")
            close_fees += execution.short_order.fee

            await self._record_trade(
                position_id,
                execution.short_order,
                position.short_exchange,
                OrderSide.SHORT,
                OrderAction.CLOSE,
            )

        # Calculate P&L
        long_pnl = Decimal("0")
        short_pnl = Decimal("0")

        if position.long_entry_price and long_close_price and position.long_size:
            long_pnl = (long_close_price - position.long_entry_price) * position.long_size

        if position.short_entry_price and short_close_price and position.short_size:
            short_pnl = (position.short_entry_price - short_close_price) * position.short_size

        total_fees = position.total_fees + close_fees
        realized_pnl = long_pnl + short_pnl + position.funding_collected - total_fees

        # Update position
        await self.position_repo.close_position(
            position_id,
            status=PositionStatus.CLOSED,
            realized_pnl=realized_pnl,
            long_close_price=long_close_price,
            short_close_price=short_close_price,
        )
        await self.position_repo.update(position_id, total_fees=total_fees)

        await self.session.commit()

        logger.info(
            "position_closed",
            position_id=position_id,
            realized_pnl=float(realized_pnl),
            funding_collected=float(position.funding_collected),
        )

        return await self.position_repo.get_by_id(position_id)

    async def mark_liquidated(
        self,
        position_id: str,
        liquidated_exchange: str,
        surviving_close_result: Optional[ExecutionResult] = None,
    ) -> Position:
        """
        Mark a position as liquidated.

        Args:
            position_id: Position ID
            liquidated_exchange: Exchange where liquidation occurred
            surviving_close_result: Result of closing the surviving leg

        Returns:
            Updated Position object
        """
        position = await self.position_repo.get_by_id(position_id)
        if not position:
            raise ValueError(f"Position not found: {position_id}")

        # Estimate loss from liquidation (simplified)
        realized_pnl = position.funding_collected - position.total_fees

        if surviving_close_result and surviving_close_result.success:
            # Add P&L from surviving leg close
            if surviving_close_result.long_order:
                order = surviving_close_result.long_order
                if position.long_entry_price and order.average_price:
                    realized_pnl += (order.average_price - position.long_entry_price) * order.filled_size
            if surviving_close_result.short_order:
                order = surviving_close_result.short_order
                if position.short_entry_price and order.average_price:
                    realized_pnl += (position.short_entry_price - order.average_price) * order.filled_size

        await self.position_repo.update(
            position_id,
            status=PositionStatus.LIQUIDATED,
            close_timestamp=datetime.now(timezone.utc),
            realized_pnl=realized_pnl,
            notes=f"Liquidated on {liquidated_exchange}",
        )

        await self.session.commit()

        logger.warning(
            "position_liquidated",
            position_id=position_id,
            liquidated_exchange=liquidated_exchange,
            realized_pnl=float(realized_pnl),
        )

        return await self.position_repo.get_by_id(position_id)

    async def record_funding_payment(
        self,
        position_id: str,
        exchange: str,
        side: OrderSide,
        funding_rate: Decimal,
        payment_usd: Decimal,
        position_size: Decimal,
    ) -> FundingEvent:
        """
        Record a funding payment for a position.

        Args:
            position_id: Position ID
            exchange: Exchange name
            side: Position side (LONG or SHORT)
            funding_rate: Funding rate
            payment_usd: Payment amount in USD
            position_size: Position size at funding time

        Returns:
            Created FundingEvent
        """
        position = await self.position_repo.get_by_id(position_id)
        if not position:
            raise ValueError(f"Position not found: {position_id}")

        event = FundingEvent(
            position_id=position_id,
            exchange=exchange,
            pair=position.pair,
            side=side,
            funding_rate=funding_rate,
            payment_usd=payment_usd,
            position_size=position_size,
        )

        event = await self.funding_repo.create(event)

        # Update position's funding collected
        await self.position_repo.add_funding(position_id, payment_usd)

        await self.session.commit()

        logger.info(
            "funding_recorded",
            position_id=position_id,
            exchange=exchange,
            rate=float(funding_rate),
            payment=float(payment_usd),
        )

        return event

    async def get_open_positions(self) -> List[Position]:
        """Get all open positions."""
        return await self.position_repo.get_open_positions()

    async def get_position(self, position_id: str) -> Optional[Position]:
        """Get a position by ID."""
        return await self.position_repo.get_by_id(position_id)

    async def get_position_for_pair(self, pair: str) -> Optional[Position]:
        """Get open position for a trading pair."""
        return await self.position_repo.get_open_position_for_pair(pair)

    async def reconcile_with_exchanges(self) -> List[str]:
        """
        Reconcile local position state with exchange positions.

        Returns:
            List of issues found (empty if all OK)
        """
        issues = []
        open_positions = await self.get_open_positions()

        for position in open_positions:
            # Check long leg
            try:
                long_exchange_pos = await self.exchanges[position.long_exchange].get_position(
                    position.pair
                )
                if not long_exchange_pos or long_exchange_pos.size == 0:
                    issues.append(
                        f"Position {position.id}: Long leg missing on {position.long_exchange}"
                    )
            except Exception as e:
                issues.append(
                    f"Position {position.id}: Error checking long leg - {e}"
                )

            # Check short leg
            try:
                short_exchange_pos = await self.exchanges[position.short_exchange].get_position(
                    position.pair
                )
                if not short_exchange_pos or short_exchange_pos.size == 0:
                    issues.append(
                        f"Position {position.id}: Short leg missing on {position.short_exchange}"
                    )
            except Exception as e:
                issues.append(
                    f"Position {position.id}: Error checking short leg - {e}"
                )

        if issues:
            logger.error("reconciliation_issues", issues=issues)
        else:
            logger.info("reconciliation_ok", positions_checked=len(open_positions))

        return issues

    async def _record_trade(
        self,
        position_id: str,
        order_result: OrderResult,
        exchange: str,
        side: OrderSide,
        action: OrderAction,
    ) -> Trade:
        """Record a trade execution."""
        trade = Trade(
            position_id=position_id,
            exchange=exchange,
            pair=order_result.symbol,
            side=side,
            action=action,
            order_type=DbOrderType.LIMIT if order_result.order_type.value == "LIMIT" else DbOrderType.MARKET,
            price=order_result.average_price or order_result.price,
            size=order_result.filled_size,
            fee=order_result.fee,
            order_id=order_result.order_id,
            status=TradeStatus.FILLED if order_result.is_filled else TradeStatus.FAILED,
            executed_at=order_result.timestamp,
        )

        return await self.trade_repo.create(trade)
