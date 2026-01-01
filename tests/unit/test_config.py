"""
Unit tests for configuration module.
"""

import os
import tempfile
from decimal import Decimal

import pytest
import yaml
from pydantic import ValidationError

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
            min_daily_spread_base=Decimal("0.0003"),
            min_daily_spread_per_10k=Decimal("0.00003"),
        )
        # For $10,000: 0.0003 + (0.00003 * 1) = 0.00033
        threshold = config.calculate_threshold(Decimal("10000"))
        assert float(threshold) == pytest.approx(0.00033)

    def test_calculate_threshold_large_size(self):
        """Test threshold calculation for large position sizes."""
        config = TradingConfig(
            symbols=["BTC/USDT:USDT"],
            min_daily_spread_base=Decimal("0.0003"),
            min_daily_spread_per_10k=Decimal("0.00003"),
        )
        # For $50,000: 0.0003 + (0.00003 * 5) = 0.00045
        threshold = config.calculate_threshold(Decimal("50000"))
        assert float(threshold) == pytest.approx(0.00045)

    def test_calculate_threshold_zero_size(self):
        """Test threshold calculation for zero position size."""
        config = TradingConfig(
            symbols=["BTC/USDT:USDT"],
            min_daily_spread_base=Decimal("0.0003"),
            min_daily_spread_per_10k=Decimal("0.00003"),
        )
        threshold = config.calculate_threshold(Decimal("0"))
        assert float(threshold) == pytest.approx(0.0003)


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
        # Note: min_daily_spread_base is the daily normalized threshold
        assert float(config.min_daily_spread_base) == pytest.approx(0.0003)
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


class TestTradingConfigSchema:
    """Tests to ensure TradingConfig schema is correct and catches invalid field names."""

    # Define the expected field names - update this when schema changes
    EXPECTED_TRADING_CONFIG_FIELDS = {
        "symbols",
        "min_daily_spread_base",
        "min_daily_spread_per_10k",
        "entry_buffer_minutes",
        "order_fill_timeout_seconds",
        "max_position_per_pair_usd",
        "negative_spread_tolerance",
        "leverage",
        "simulation_mode",
        "min_simulation_hours",
    }

    def test_trading_config_has_expected_fields(self):
        """Verify TradingConfig has all expected field names."""
        actual_fields = set(TradingConfig.model_fields.keys())

        assert self.EXPECTED_TRADING_CONFIG_FIELDS == actual_fields, (
            f"TradingConfig fields mismatch.\n"
            f"Missing: {self.EXPECTED_TRADING_CONFIG_FIELDS - actual_fields}\n"
            f"Extra: {actual_fields - self.EXPECTED_TRADING_CONFIG_FIELDS}"
        )

    def test_old_field_names_rejected(self):
        """Ensure old field names (min_spread_base, min_spread_per_10k) are rejected."""
        # Old field names should cause validation error
        with pytest.raises(ValidationError) as exc_info:
            TradingConfig(
                symbols=["BTC/USDT:USDT"],
                min_spread_base=Decimal("0.0001"),  # OLD NAME - should fail
            )
        assert "min_spread_base" in str(exc_info.value)

        with pytest.raises(ValidationError) as exc_info:
            TradingConfig(
                symbols=["BTC/USDT:USDT"],
                min_spread_per_10k=Decimal("0.00001"),  # OLD NAME - should fail
            )
        assert "min_spread_per_10k" in str(exc_info.value)

    def test_new_field_names_accepted(self):
        """Ensure new field names (min_daily_spread_*) are accepted."""
        config = TradingConfig(
            symbols=["BTC/USDT:USDT"],
            min_daily_spread_base=Decimal("0.0003"),
            min_daily_spread_per_10k=Decimal("0.00003"),
        )
        assert float(config.min_daily_spread_base) == pytest.approx(0.0003)
        assert float(config.min_daily_spread_per_10k) == pytest.approx(0.00003)

    def test_config_example_yaml_uses_correct_field_names(self):
        """Verify config.example.yaml uses the correct field names."""
        example_path = os.path.join(
            os.path.dirname(__file__),
            "..", "..", "config", "config.example.yaml"
        )

        with open(example_path, "r") as f:
            config_data = yaml.safe_load(f)

        trading_config = config_data.get("trading", {})

        # Should have new field names
        assert "min_daily_spread_base" in trading_config, (
            "config.example.yaml should use 'min_daily_spread_base'"
        )
        assert "min_daily_spread_per_10k" in trading_config, (
            "config.example.yaml should use 'min_daily_spread_per_10k'"
        )

        # Should NOT have old field names
        assert "min_spread_base" not in trading_config, (
            "config.example.yaml should NOT use deprecated 'min_spread_base'"
        )
        assert "min_spread_per_10k" not in trading_config, (
            "config.example.yaml should NOT use deprecated 'min_spread_per_10k'"
        )

    def test_config_example_yaml_is_valid(self):
        """Verify config.example.yaml can be loaded as valid TradingConfig."""
        example_path = os.path.join(
            os.path.dirname(__file__),
            "..", "..", "config", "config.example.yaml"
        )

        with open(example_path, "r") as f:
            config_data = yaml.safe_load(f)

        trading_data = config_data.get("trading", {})

        # Should not raise ValidationError
        config = TradingConfig(**trading_data)
        assert config.symbols is not None
        assert len(config.symbols) > 0


class TestConfigResponseSchema:
    """Tests to ensure API ConfigResponse schema matches TradingConfig."""

    def test_config_response_has_daily_spread_fields(self):
        """Verify ConfigResponse uses the correct field names."""
        from backend.api.schemas import ConfigResponse

        fields = ConfigResponse.model_fields.keys()

        # Should have new field names
        assert "min_daily_spread_base" in fields, (
            "ConfigResponse should have 'min_daily_spread_base'"
        )
        assert "min_daily_spread_per_10k" in fields, (
            "ConfigResponse should have 'min_daily_spread_per_10k'"
        )

        # Should NOT have old field names
        assert "min_spread_base" not in fields, (
            "ConfigResponse should NOT have deprecated 'min_spread_base'"
        )
        assert "min_spread_per_10k" not in fields, (
            "ConfigResponse should NOT have deprecated 'min_spread_per_10k'"
        )
