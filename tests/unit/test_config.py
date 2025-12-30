"""
Unit tests for configuration module.
"""

import os
import tempfile
from decimal import Decimal

import pytest
import yaml

from backend.config.schema import (
    Config,
    ExchangeConfig,
    TradingConfig,
    DatabaseConfig,
    TelegramConfig,
    APIConfig,
)
from backend.config.loader import load_config


class TestTradingConfig:
    """Tests for TradingConfig."""

    def test_calculate_threshold_small_size(self):
        """Test threshold calculation for small position sizes."""
        config = TradingConfig(
            symbols=["BTC/USDT:USDT"],
            min_spread_base=Decimal("0.0001"),
            min_spread_per_10k=Decimal("0.00001"),
        )
        # For $10,000: 0.0001 + (0.00001 * 1) = 0.00011
        threshold = config.calculate_threshold(Decimal("10000"))
        assert float(threshold) == pytest.approx(0.00011)

    def test_calculate_threshold_large_size(self):
        """Test threshold calculation for large position sizes."""
        config = TradingConfig(
            symbols=["BTC/USDT:USDT"],
            min_spread_base=Decimal("0.0001"),
            min_spread_per_10k=Decimal("0.00001"),
        )
        # For $50,000: 0.0001 + (0.00001 * 5) = 0.00015
        threshold = config.calculate_threshold(Decimal("50000"))
        assert float(threshold) == pytest.approx(0.00015)

    def test_calculate_threshold_zero_size(self):
        """Test threshold calculation for zero position size."""
        config = TradingConfig(
            symbols=["BTC/USDT:USDT"],
            min_spread_base=Decimal("0.0001"),
            min_spread_per_10k=Decimal("0.00001"),
        )
        threshold = config.calculate_threshold(Decimal("0"))
        assert float(threshold) == pytest.approx(0.0001)


class TestConfig:
    """Tests for main Config class."""

    def test_config_creation(self):
        """Test that config can be created with all required fields."""
        config = Config(
            exchanges={
                "binance": ExchangeConfig(api_key="key", api_secret="secret"),
                "bybit": ExchangeConfig(api_key="key", api_secret="secret"),
            },
            trading=TradingConfig(symbols=["BTC/USDT:USDT", "ETH/USDT:USDT"]),
        )
        assert config.trading.simulation_mode is True
        assert "binance" in config.exchanges
        assert "bybit" in config.exchanges
        assert len(config.trading.symbols) == 2

    def test_config_exchange_access(self):
        """Test accessing exchange configurations."""
        config = Config(
            exchanges={
                "binance": ExchangeConfig(api_key="test_key", api_secret="test_secret", testnet=True),
            }
        )
        binance_config = config.exchanges.get("binance")
        assert binance_config is not None
        assert binance_config.testnet is True
        assert binance_config.api_key.get_secret_value() == "test_key"

    def test_config_trading_defaults(self):
        """Test trading config default values."""
        config = TradingConfig(symbols=["BTC/USDT:USDT"])
        assert float(config.min_spread_base) == pytest.approx(0.0001)
        assert config.entry_buffer_minutes == 20
        assert config.order_fill_timeout_seconds == 30
        assert float(config.max_position_per_pair_usd) == 50000.0
        assert config.simulation_mode is True


class TestConfigLoader:
    """Tests for configuration loading."""

    def test_load_config_from_yaml(self):
        """Test loading configuration from YAML file."""
        config_data = {
            "exchanges": {
                "binance": {
                    "api_key": "test_key",
                    "api_secret": "test_secret",
                    "testnet": True,
                }
            },
            "trading": {
                "symbols": ["BTC/USDT:USDT"],
                "simulation_mode": True,
            },
            "database": {
                "driver": "sqlite",
                "sqlite_path": ":memory:",
            },
            "telegram": {
                "bot_token": "test_token",
                "chat_id": "test_chat",
                "enabled": False,
            },
            "api": {
                "host": "127.0.0.1",
                "port": 8000,
            },
        }

        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            yaml.dump(config_data, f)
            temp_path = f.name

        try:
            config = load_config(temp_path)
            assert config.trading.symbols == ["BTC/USDT:USDT"]
            assert config.trading.simulation_mode is True
            assert "binance" in config.exchanges
        finally:
            os.unlink(temp_path)

    def test_load_config_file_not_found(self):
        """Test loading configuration from non-existent file."""
        with pytest.raises(FileNotFoundError):
            load_config("/nonexistent/path/config.yaml")

    def test_leverage_configuration(self):
        """Test leverage configuration access."""
        from backend.config.schema import LeverageConfig
        config = TradingConfig(
            symbols=["BTC/USDT:USDT"],
            leverage={"binance": LeverageConfig(default=5)},
        )
        assert config.leverage["binance"].default == 5
