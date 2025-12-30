"""
Exchange factory for creating exchange adapters.

Provides a unified way to instantiate exchange connections
based on configuration.
"""

from typing import Dict, Type

from ..config.schema import Config, ExchangeConfig
from ..utils.logging import get_logger
from .base import ExchangeAdapter
from .binance import BinanceAdapter
from .bybit import BybitAdapter

logger = get_logger(__name__)


# Registry of available exchange adapters
EXCHANGE_ADAPTERS: Dict[str, Type[ExchangeAdapter]] = {
    "binance": BinanceAdapter,
    "bybit": BybitAdapter,
}


def create_exchange(
    name: str,
    exchange_config: ExchangeConfig,
    force_testnet: bool = False,
) -> ExchangeAdapter:
    """
    Create an exchange adapter instance.

    Args:
        name: Exchange name (e.g., 'binance', 'bybit')
        exchange_config: Exchange configuration with credentials
        force_testnet: Force testnet mode regardless of config

    Returns:
        Initialized exchange adapter (not connected)

    Raises:
        ValueError: If exchange is not supported
    """
    name_lower = name.lower()

    if name_lower not in EXCHANGE_ADAPTERS:
        available = ", ".join(EXCHANGE_ADAPTERS.keys())
        raise ValueError(f"Unsupported exchange: {name}. Available: {available}")

    adapter_class = EXCHANGE_ADAPTERS[name_lower]

    testnet = force_testnet or exchange_config.testnet

    adapter = adapter_class(
        api_key=exchange_config.api_key.get_secret_value(),
        api_secret=exchange_config.api_secret.get_secret_value(),
        testnet=testnet,
        rate_limit_buffer=exchange_config.rate_limit_buffer,
    )

    logger.info(
        "exchange_adapter_created",
        exchange=name,
        testnet=testnet,
    )

    return adapter


async def create_exchanges(
    config: Config,
    force_testnet: bool = False,
) -> Dict[str, ExchangeAdapter]:
    """
    Create and connect all configured exchanges.

    Args:
        config: Application configuration
        force_testnet: Force testnet mode for all exchanges

    Returns:
        Dict mapping exchange name to connected adapter
    """
    # Determine if we should force testnet (simulation mode)
    use_testnet = force_testnet or config.is_simulation_mode()

    exchanges: Dict[str, ExchangeAdapter] = {}

    for name, exchange_config in config.exchanges.items():
        try:
            adapter = create_exchange(name, exchange_config, use_testnet)
            await adapter.connect()
            exchanges[name] = adapter

            logger.info(
                "exchange_connected",
                exchange=name,
                testnet=adapter.is_testnet,
            )
        except Exception as e:
            logger.error(
                "exchange_connection_failed",
                exchange=name,
                error=str(e),
            )
            # Clean up already connected exchanges
            for connected in exchanges.values():
                try:
                    await connected.disconnect()
                except Exception:
                    pass
            raise

    return exchanges


async def disconnect_all(exchanges: Dict[str, ExchangeAdapter]) -> None:
    """
    Disconnect all exchanges.

    Args:
        exchanges: Dict of connected exchange adapters
    """
    for name, adapter in exchanges.items():
        try:
            await adapter.disconnect()
            logger.info("exchange_disconnected", exchange=name)
        except Exception as e:
            logger.warning(
                "exchange_disconnect_failed",
                exchange=name,
                error=str(e),
            )


def get_supported_exchanges() -> list:
    """Get list of supported exchange names."""
    return list(EXCHANGE_ADAPTERS.keys())


def is_exchange_supported(name: str) -> bool:
    """Check if an exchange is supported."""
    return name.lower() in EXCHANGE_ADAPTERS
