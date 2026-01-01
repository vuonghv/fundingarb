"""
Pydantic configuration models for the trading system.

All configuration is validated at startup to catch errors early.
"""

from typing import Dict, List, Optional
from decimal import Decimal
from pydantic import BaseModel, ConfigDict, SecretStr, Field, field_validator
from pydantic_settings import BaseSettings


class ExchangeConfig(BaseModel):
    """Configuration for a single exchange."""

    api_key: SecretStr
    api_secret: SecretStr
    testnet: bool = False

    # Optional exchange-specific settings
    rate_limit_buffer: float = Field(default=0.1, ge=0, le=1)  # 10% buffer on rate limits


class LeverageConfig(BaseModel):
    """Leverage settings per symbol."""

    default: int = Field(default=5, ge=1, le=125)
    overrides: Dict[str, int] = Field(default_factory=dict)  # symbol -> leverage

    def get_leverage(self, symbol: str) -> int:
        """Get leverage for a symbol, falling back to default."""
        return self.overrides.get(symbol, self.default)


class TradingConfig(BaseModel):
    """Trading strategy configuration."""

    # Reject unknown field names to catch config errors early
    model_config = ConfigDict(extra="forbid")

    # Symbols to monitor and trade
    symbols: List[str] = Field(default_factory=lambda: ["BTC/USDT:USDT", "ETH/USDT:USDT"])

    # Spread threshold settings (dynamic: base + per_10k * size/10000)
    # IMPORTANT: These thresholds are compared against DAILY normalized spread.
    # This ensures correct comparison across exchanges with different funding intervals.
    # Example: 0.0003 (0.03% daily) ≈ 0.01% per 8h funding or 0.00125% per 1h funding
    min_daily_spread_base: Decimal = Field(
        default=Decimal("0.0003"),  # 0.03% daily
        description="Minimum daily spread required for entry (normalized to 24h basis)"
    )
    min_daily_spread_per_10k: Decimal = Field(
        default=Decimal("0.00003"),  # 0.003% daily per $10k
        description="Additional daily spread required per $10k position size"
    )

    # Entry timing
    entry_buffer_minutes: int = Field(default=20, ge=1, le=60)

    # Order execution
    order_fill_timeout_seconds: int = Field(default=30, ge=5, le=300)

    # Position limits
    max_position_per_pair_usd: Decimal = Field(default=Decimal("50000"))

    # Exit settings
    negative_spread_tolerance: Decimal = Field(default=Decimal("-0.0001"))  # -0.01%

    # Leverage per exchange
    leverage: Dict[str, LeverageConfig] = Field(default_factory=dict)

    # Simulation mode settings
    simulation_mode: bool = Field(default=True)
    min_simulation_hours: int = Field(default=24, ge=0)

    @field_validator("symbols")
    @classmethod
    def validate_symbols(cls, v: List[str]) -> List[str]:
        """Ensure symbols are in correct format."""
        for symbol in v:
            if "/" not in symbol:
                raise ValueError(f"Invalid symbol format: {symbol}. Expected format: BTC/USDT:USDT")
        return v

    def calculate_threshold(self, position_size_usd: Decimal) -> Decimal:
        """
        Calculate dynamic daily spread threshold based on position size.

        Returns the minimum daily spread required for entry. This threshold
        is compared against the daily normalized spread between exchanges.
        """
        return self.min_daily_spread_base + (self.min_daily_spread_per_10k * (position_size_usd / Decimal("10000")))


class DatabaseConfig(BaseModel):
    """Database connection configuration."""

    # Connection type: sqlite or postgresql
    driver: str = Field(default="sqlite")

    # SQLite settings
    sqlite_path: str = Field(default="data/fundingarb.db")

    # PostgreSQL settings
    host: str = Field(default="localhost")
    port: int = Field(default=5432)
    database: str = Field(default="fundingarb")
    username: str = Field(default="")
    password: SecretStr = Field(default=SecretStr(""))

    # Connection pool settings
    pool_size: int = Field(default=5, ge=1, le=20)
    max_overflow: int = Field(default=10, ge=0, le=50)

    def get_connection_url(self) -> str:
        """Generate SQLAlchemy connection URL."""
        if self.driver == "sqlite":
            return f"sqlite+aiosqlite:///{self.sqlite_path}"
        elif self.driver == "postgresql":
            password = self.password.get_secret_value()
            return f"postgresql+asyncpg://{self.username}:{password}@{self.host}:{self.port}/{self.database}"
        else:
            raise ValueError(f"Unsupported database driver: {self.driver}")


class TelegramConfig(BaseModel):
    """Telegram alert configuration."""

    enabled: bool = Field(default=False)
    bot_token: SecretStr = Field(default=SecretStr(""))
    chat_id: str = Field(default="")

    # Alert settings
    send_info: bool = Field(default=True)
    send_warning: bool = Field(default=True)
    send_critical: bool = Field(default=True)


class APIConfig(BaseModel):
    """API server configuration."""

    host: str = Field(default="0.0.0.0")
    port: int = Field(default=8000)

    # CORS settings
    cors_origins: List[str] = Field(default_factory=lambda: ["*"])

    # WebSocket settings
    ws_heartbeat_interval: int = Field(default=30)  # seconds


class Config(BaseModel):
    """Root configuration model."""

    # Prevent accidental logging of secrets
    model_config = ConfigDict(
        json_encoders={SecretStr: lambda v: "***REDACTED***"}
    )

    # Exchange configurations keyed by exchange name
    exchanges: Dict[str, ExchangeConfig] = Field(default_factory=dict)

    # Trading strategy settings
    trading: TradingConfig = Field(default_factory=TradingConfig)

    # Database settings
    database: DatabaseConfig = Field(default_factory=DatabaseConfig)

    # Telegram alerts
    telegram: TelegramConfig = Field(default_factory=TelegramConfig)

    # API server settings
    api: APIConfig = Field(default_factory=APIConfig)

    def get_exchange_names(self) -> List[str]:
        """Get list of configured exchange names."""
        return list(self.exchanges.keys())

    def is_simulation_mode(self) -> bool:
        """Check if running in simulation mode."""
        return self.trading.simulation_mode
