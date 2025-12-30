"""
Configuration API routes.
"""

from typing import Optional
from fastapi import APIRouter, HTTPException, Request

from ..schemas import ConfigUpdateRequest, ConfigResponse

router = APIRouter()


@router.get("", response_model=ConfigResponse)
async def get_config(request: Request):
    """
    Get current configuration.

    Returns sanitized configuration (no secrets).
    """
    config = request.app.state.config

    if not config:
        return ConfigResponse(
            symbols=["BTC/USDT:USDT", "ETH/USDT:USDT"],
            exchanges=[],
            min_spread_base=0.0001,
            min_spread_per_10k=0.00001,
            entry_buffer_minutes=20,
            order_fill_timeout_seconds=30,
            max_position_per_pair_usd=50000.0,
            negative_spread_tolerance=-0.0001,
            leverage={},
            simulation_mode=True,
        )

    trading = config.trading
    return ConfigResponse(
        symbols=trading.symbols if trading else [],
        exchanges=list(config.exchanges.keys()) if config.exchanges else [],
        min_spread_base=trading.min_spread_base if trading else 0.0001,
        min_spread_per_10k=trading.min_spread_per_10k if trading else 0.00001,
        entry_buffer_minutes=trading.entry_buffer_minutes if trading else 20,
        order_fill_timeout_seconds=trading.order_fill_timeout_seconds if trading else 30,
        max_position_per_pair_usd=trading.max_position_per_pair_usd if trading else 50000.0,
        negative_spread_tolerance=trading.negative_spread_tolerance if trading else -0.0001,
        leverage={},
        simulation_mode=trading.simulation_mode if trading else True,
    )


@router.patch("", response_model=ConfigResponse)
async def update_config(request: ConfigUpdateRequest):
    """
    Update configuration (hot reload).

    Only trading-related settings can be updated without restart.
    Exchange credentials require a restart.
    """
    # Validate and apply updates
    # This needs to be wired to the actual config

    raise HTTPException(
        status_code=501,
        detail="Configuration update requires config manager integration"
    )


@router.get("/supported-exchanges")
async def get_supported_exchanges():
    """Get list of supported exchanges."""
    from ...exchanges.factory import get_supported_exchanges
    return {"exchanges": get_supported_exchanges()}


@router.get("/leverage/{exchange}")
async def get_leverage_config(exchange: str):
    """Get leverage configuration for an exchange."""
    # This needs config integration
    return {
        "exchange": exchange,
        "default": 5,
        "overrides": {},
    }


@router.put("/leverage/{exchange}")
async def set_leverage_config(exchange: str, default: int, overrides: Optional[dict] = None):
    """Set leverage configuration for an exchange."""
    if default < 1 or default > 125:
        raise HTTPException(
            status_code=400,
            detail="Leverage must be between 1 and 125"
        )

    raise HTTPException(
        status_code=501,
        detail="Leverage update requires config manager integration"
    )


@router.post("/symbols/add")
async def add_symbol(symbol: str):
    """Add a symbol to monitor."""
    if "/" not in symbol:
        raise HTTPException(
            status_code=400,
            detail="Invalid symbol format. Expected: BTC/USDT:USDT"
        )

    raise HTTPException(
        status_code=501,
        detail="Symbol management requires config manager integration"
    )


@router.post("/symbols/remove")
async def remove_symbol(symbol: str):
    """Remove a symbol from monitoring."""
    raise HTTPException(
        status_code=501,
        detail="Symbol management requires config manager integration"
    )
