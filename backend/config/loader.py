"""
Configuration loader with encryption support.

Supports loading from:
- Plain YAML files (development)
- Encrypted YAML files (production)
- Environment variable overrides
"""

import os
import yaml
from pathlib import Path
from typing import Optional

from .schema import Config
from ..utils.encryption import decrypt_data, encrypt_data, is_encrypted


def load_config(
    config_path: str = "config/config.yaml",
    password: Optional[str] = None,
) -> Config:
    """
    Load configuration from file.

    Args:
        config_path: Path to configuration file (YAML or encrypted)
        password: Master password for encrypted configs (or from FUNDINGARB_MASTER_PASSWORD env)

    Returns:
        Validated Config object

    Raises:
        FileNotFoundError: If config file doesn't exist
        ValueError: If encrypted but no password provided
        ValidationError: If config validation fails
    """
    path = Path(config_path)

    if not path.exists():
        raise FileNotFoundError(f"Configuration file not found: {config_path}")

    # Read file content
    with open(path, "rb") as f:
        content = f.read()

    # Check if encrypted
    if is_encrypted(content):
        # Get password from argument or environment
        master_password = password or os.environ.get("FUNDINGARB_MASTER_PASSWORD")
        if not master_password:
            raise ValueError(
                "Configuration is encrypted but no password provided. "
                "Set FUNDINGARB_MASTER_PASSWORD environment variable or pass password argument."
            )

        # Decrypt
        content = decrypt_data(content, master_password)

    # Parse YAML
    config_dict = yaml.safe_load(content.decode("utf-8") if isinstance(content, bytes) else content)

    if config_dict is None:
        config_dict = {}

    # Apply environment variable overrides
    config_dict = _apply_env_overrides(config_dict)

    # Validate and return
    return Config.model_validate(config_dict)


def save_encrypted_config(
    config: Config,
    output_path: str,
    password: str,
) -> None:
    """
    Save configuration to an encrypted file.

    Args:
        config: Configuration object to save
        output_path: Path to save encrypted config
        password: Master password for encryption
    """
    # Convert to dict, excluding unset values
    config_dict = config.model_dump(mode="json", exclude_unset=False)

    # Convert SecretStr values to plain strings for serialization
    config_dict = _unmask_secrets(config_dict, config)

    # Serialize to YAML
    yaml_content = yaml.dump(config_dict, default_flow_style=False, sort_keys=False)

    # Encrypt
    encrypted = encrypt_data(yaml_content.encode("utf-8"), password)

    # Write to file
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)

    with open(path, "wb") as f:
        f.write(encrypted)


def _apply_env_overrides(config_dict: dict) -> dict:
    """Apply environment variable overrides to config."""

    # Database driver override
    if db_driver := os.environ.get("FUNDINGARB_DB_DRIVER"):
        config_dict.setdefault("database", {})["driver"] = db_driver

    # PostgreSQL connection from DATABASE_URL (common pattern)
    if database_url := os.environ.get("DATABASE_URL"):
        # Parse DATABASE_URL (postgresql://user:pass@host:port/db)
        if database_url.startswith("postgresql://"):
            config_dict.setdefault("database", {})["driver"] = "postgresql"
            # Let SQLAlchemy handle the URL parsing

    # Telegram settings
    if telegram_token := os.environ.get("TELEGRAM_BOT_TOKEN"):
        config_dict.setdefault("telegram", {})["bot_token"] = telegram_token
        config_dict["telegram"]["enabled"] = True

    if telegram_chat := os.environ.get("TELEGRAM_CHAT_ID"):
        config_dict.setdefault("telegram", {})["chat_id"] = telegram_chat

    # API settings
    if api_host := os.environ.get("FUNDINGARB_API_HOST"):
        config_dict.setdefault("api", {})["host"] = api_host

    if api_port := os.environ.get("FUNDINGARB_API_PORT"):
        config_dict.setdefault("api", {})["port"] = int(api_port)

    # Simulation mode override
    if sim_mode := os.environ.get("FUNDINGARB_SIMULATION_MODE"):
        config_dict.setdefault("trading", {})["simulation_mode"] = sim_mode.lower() in ("true", "1", "yes")

    return config_dict


def _unmask_secrets(config_dict: dict, config: Config) -> dict:
    """Convert SecretStr fields back to plain strings for serialization."""

    # Handle exchange secrets
    for name, exchange_config in config.exchanges.items():
        if name in config_dict.get("exchanges", {}):
            config_dict["exchanges"][name]["api_key"] = exchange_config.api_key.get_secret_value()
            config_dict["exchanges"][name]["api_secret"] = exchange_config.api_secret.get_secret_value()

    # Handle database password
    if "database" in config_dict and config.database.password:
        config_dict["database"]["password"] = config.database.password.get_secret_value()

    # Handle telegram token
    if "telegram" in config_dict and config.telegram.bot_token:
        config_dict["telegram"]["bot_token"] = config.telegram.bot_token.get_secret_value()

    return config_dict


def create_example_config() -> str:
    """Generate example configuration YAML."""

    example = """# Funding Rate Arbitrage Configuration
# Copy this file to config/config.yaml and fill in your values

# Exchange API credentials
exchanges:
  binance:
    api_key: "your-binance-api-key"
    api_secret: "your-binance-api-secret"
    testnet: true  # Use testnet for simulation mode

  bybit:
    api_key: "your-bybit-api-key"
    api_secret: "your-bybit-api-secret"
    testnet: true

# Trading strategy settings
trading:
  # Symbols to monitor (ccxt unified format)
  symbols:
    - "BTC/USDT:USDT"
    - "ETH/USDT:USDT"
    - "SOL/USDT:USDT"

  # Daily spread threshold: base + per_10k * (size / 10000)
  # All spreads are normalized to daily basis for cross-exchange comparison
  min_daily_spread_base: 0.0003      # 0.03% daily (≈0.01% per 8h funding)
  min_daily_spread_per_10k: 0.00003  # 0.003% daily per $10k

  # Entry timing (minutes before funding)
  entry_buffer_minutes: 20

  # Order execution timeout (seconds)
  order_fill_timeout_seconds: 30

  # Maximum position size per pair (USD)
  max_position_per_pair_usd: 50000

  # Negative spread tolerance before closing
  negative_spread_tolerance: -0.0001  # -0.01%

  # Leverage settings per exchange
  leverage:
    binance:
      default: 5
      overrides:
        "BTC/USDT:USDT": 3
        "ETH/USDT:USDT": 5
    bybit:
      default: 5

  # Simulation mode (REQUIRED before live trading)
  simulation_mode: true
  min_simulation_hours: 24

# Database settings
database:
  driver: sqlite  # sqlite or postgresql
  sqlite_path: data/fundingarb.db

  # PostgreSQL settings (if driver: postgresql)
  # host: localhost
  # port: 5432
  # database: fundingarb
  # username: your_username
  # password: your_password

# Telegram alerts
telegram:
  enabled: false
  bot_token: "your-telegram-bot-token"
  chat_id: "your-chat-id"
  send_info: true
  send_warning: true
  send_critical: true

# API server settings
api:
  host: "0.0.0.0"
  port: 8000
  cors_origins:
    - "*"
  ws_heartbeat_interval: 30
"""
    return example
