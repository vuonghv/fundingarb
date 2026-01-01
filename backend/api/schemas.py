"""
Pydantic schemas for API request/response models.
"""

from datetime import datetime
from decimal import Decimal
from typing import Dict, List, Optional, Any
from pydantic import BaseModel, Field


# ==================== Position Schemas ====================

class TradeResponse(BaseModel):
    """Trade execution response."""
    id: str
    position_id: str
    exchange: str
    pair: str
    side: str  # LONG or SHORT
    action: str  # OPEN or CLOSE
    order_type: str
    price: Optional[float]
    size: float
    fee: float
    order_id: Optional[str]
    status: str
    executed_at: Optional[datetime]

    class Config:
        from_attributes = True


class FundingEventResponse(BaseModel):
    """Funding event response."""
    id: str
    position_id: str
    exchange: str
    pair: str
    side: str
    funding_rate: float
    payment_usd: float
    position_size: float
    timestamp: datetime

    class Config:
        from_attributes = True


class PositionResponse(BaseModel):
    """Position response model."""
    id: str
    pair: str
    long_exchange: str
    short_exchange: str
    long_entry_price: Optional[float]
    short_entry_price: Optional[float]
    size_usd: float
    long_size: Optional[float]
    short_size: Optional[float]
    leverage_long: int
    leverage_short: int
    entry_timestamp: datetime
    entry_funding_spread: Optional[float]
    status: str
    close_timestamp: Optional[datetime]
    realized_pnl: Optional[float]
    funding_collected: float
    total_fees: float
    unrealized_pnl: Optional[float] = None

    class Config:
        from_attributes = True


class PositionListResponse(BaseModel):
    """List of positions."""
    positions: List[PositionResponse]
    total: int


class OpenPositionRequest(BaseModel):
    """Request to manually open a position."""
    symbol: str = Field(..., description="Trading pair symbol (e.g., BTC/USDT:USDT)")
    long_exchange: str = Field(..., description="Exchange for long position")
    short_exchange: str = Field(..., description="Exchange for short position")
    size_usd: float = Field(..., gt=0, description="Position size in USD")


class ClosePositionRequest(BaseModel):
    """Request to close a position."""
    reason: Optional[str] = Field(None, description="Optional reason for closing")


# ==================== Engine Schemas ====================

class EngineStatusResponse(BaseModel):
    """Engine status response."""
    state: str
    simulation_mode: bool
    connected_exchanges: List[str]
    monitored_symbols: List[str]
    open_positions: int
    last_scan_time: Optional[datetime]
    last_opportunity_time: Optional[datetime]
    pending_orders: int
    kill_switch_active: bool
    error_message: Optional[str]


class EngineActionResponse(BaseModel):
    """Response for engine actions."""
    success: bool
    message: str
    state: Optional[str] = None


class KillSwitchRequest(BaseModel):
    """Request to activate kill switch."""
    reason: str = Field(default="Manual activation")
    confirm: bool = Field(..., description="Must be true to activate")


# ==================== Funding Rate Schemas ====================

class FundingRateResponse(BaseModel):
    """Funding rate for a symbol on an exchange."""
    exchange: str
    symbol: str
    rate: float
    rate_percent: float
    annualized_rate: float
    predicted_rate: Optional[float]
    next_funding_time: datetime
    timestamp: datetime


class FundingRatesResponse(BaseModel):
    """All funding rates."""
    rates: Dict[str, Dict[str, FundingRateResponse]]
    last_update: datetime


# ==================== Opportunity Schemas ====================

class OpportunityResponse(BaseModel):
    """Arbitrage opportunity."""
    symbol: str
    long_exchange: str
    short_exchange: str
    long_rate: float
    short_rate: float
    spread: float
    spread_percent: float
    expected_profit_per_funding: float
    expected_daily_profit: float
    annualized_apr: float
    next_funding_time: datetime
    seconds_to_funding: float
    is_urgent: bool


class OpportunitiesResponse(BaseModel):
    """List of opportunities."""
    opportunities: List[OpportunityResponse]
    threshold: float


# ==================== Configuration Schemas ====================

class ConfigUpdateRequest(BaseModel):
    """Request to update configuration."""
    # Trading settings (hot reloadable)
    symbols: Optional[List[str]] = None
    min_daily_spread_base: Optional[float] = None  # Daily normalized threshold
    min_daily_spread_per_10k: Optional[float] = None  # Daily normalized per $10k
    entry_buffer_minutes: Optional[int] = None
    order_fill_timeout_seconds: Optional[int] = None
    max_position_per_pair_usd: Optional[float] = None
    negative_spread_tolerance: Optional[float] = None
    leverage: Optional[Dict[str, Dict[str, int]]] = None


class ConfigResponse(BaseModel):
    """Current configuration (sanitized)."""
    symbols: List[str]
    exchanges: List[str] = []
    min_daily_spread_base: float  # Daily normalized threshold (0.0003 = 0.03% daily)
    min_daily_spread_per_10k: float  # Additional daily spread per $10k
    entry_buffer_minutes: int
    order_fill_timeout_seconds: int
    max_position_per_pair_usd: float
    negative_spread_tolerance: float
    leverage: Dict[str, Any]
    simulation_mode: bool


# ==================== Risk Schemas ====================

class RiskStatusResponse(BaseModel):
    """Risk management status."""
    kill_switch_active: bool
    kill_switch_activated_at: Optional[datetime]
    trading_enabled: bool
    paused_pairs: Dict[str, datetime]
    max_position_per_pair: float


# ==================== Statistics Schemas ====================

class StatsResponse(BaseModel):
    """Trading statistics."""
    total_positions: int
    open_positions: int
    closed_positions: int
    total_realized_pnl: float
    total_funding_collected: float
    total_fees_paid: float
    win_rate: float
    average_hold_time_hours: Optional[float]


# ==================== Health Schemas ====================

class HealthCheckResponse(BaseModel):
    """Health check response."""
    status: str  # "healthy", "degraded", "unhealthy"
    database: bool
    exchanges: Dict[str, bool]
    engine_running: bool
    timestamp: datetime


# ==================== WebSocket Event Schemas ====================

class WSEvent(BaseModel):
    """Base WebSocket event."""
    type: str
    data: Dict[str, Any]
    timestamp: datetime = Field(default_factory=lambda: datetime.now())


class WSPositionUpdate(BaseModel):
    """Position update event data."""
    position_id: str
    status: str
    unrealized_pnl: Optional[float]
    funding_collected: float


class WSFundingRateUpdate(BaseModel):
    """Funding rate update event data."""
    exchange: str
    pair: str
    rate: float
    predicted: Optional[float]
    next_funding_time: str


class WSTradeExecuted(BaseModel):
    """Trade executed event data."""
    position_id: str
    exchange: str
    side: str
    price: float
    size: float
    fee: float


class WSEngineStatus(BaseModel):
    """Engine status event data."""
    status: str
    connected_exchanges: List[str]
    last_scan: Optional[str]
    error: Optional[str]


class WSAlert(BaseModel):
    """Alert event data."""
    severity: str
    title: str
    message: str
    timestamp: str
