"""
Funding Rate Arbitrage Trading Engine - Main Entry Point

This is the main entry point for the trading system.
It initializes all components and starts the trading engine.
"""

import asyncio
import signal
import sys
from pathlib import Path
from typing import Optional

from .config import load_config, Config
from .utils.logging import setup_logging, get_logger

# Will be imported after other modules are created
from .database import init_database, get_session
from .exchanges import create_exchanges
from .engine import TradingCoordinator
from .api import create_app, ServerWrapper
from .alerts import create_alert_service

logger = get_logger(__name__)


class Application:
    """Main application class managing all components."""

    def __init__(self, config: Config):
        self.config = config
        self._shutdown_event = asyncio.Event()
        self._running = False

        # Components (initialized in start())
        self.exchanges: dict = {}
        self.coordinator = None
        self.api_server = None
        self.alert_service = None

    async def start(self) -> None:
        """Initialize and start all components."""
        logger.info(
            "starting_application",
            simulation_mode=self.config.is_simulation_mode(),
            exchanges=self.config.get_exchange_names(),
        )

        try:
            # Create data directory if needed
            if self.config.database.driver == "sqlite":
                Path(self.config.database.sqlite_path).parent.mkdir(parents=True, exist_ok=True)

            # Initialize database
            await init_database(self.config.database)
            logger.info("database_initialized")

            # Initialize alert service
            self.alert_service = create_alert_service(self.config.telegram)
            logger.info("alert_service_initialized")

            # Initialize exchange connections
            self.exchanges = await create_exchanges(self.config)
            logger.info("exchanges_connected", count=len(self.config.exchanges))

            # Initialize trading coordinator
            self.coordinator = TradingCoordinator(
                config=self.config.trading,
                exchanges=self.exchanges,
                alert_callback=self.alert_service.send,
            )
            logger.info("trading_coordinator_initialized")

            # Reconcile state with exchanges on startup
            issues = await self.coordinator.reconcile_state()
            if issues:
                logger.error("state_reconciliation_failed", issues=issues)
                raise RuntimeError("State mismatch detected. Manual review required.")

            # Start trading engine
            await self.coordinator.start()
            logger.info("trading_engine_started")

            # Start API server
            app = create_app(self.config, self.coordinator, self.exchanges)
            self.api_server = ServerWrapper(app)
            asyncio.create_task(self.api_server.serve(self.config.api))
            logger.info("api_server_started", host=self.config.api.host, port=self.config.api.port)

            self._running = True

            # Send startup notification
            if self.alert_service:
                await self.alert_service.send_info(
                    "System Started",
                    f"Trading engine started in {'SIMULATION' if self.config.is_simulation_mode() else 'LIVE'} mode"
                )

        except Exception as e:
            logger.exception("startup_failed", error=str(e))
            raise

    async def stop(self) -> None:
        """Gracefully stop all components."""
        if not self._running:
            return

        logger.info("stopping_application")
        self._running = False

        try:
            # Stop trading engine first
            if self.coordinator:
                await self.coordinator.stop()
            logger.info("trading_engine_stopped")

            # Save checkpoint
            if self.coordinator:
                await self.coordinator.save_checkpoint()
            logger.info("checkpoint_saved")

            # Close exchange connections
            for name, exchange in self.exchanges.items():
                try:
                    await exchange.disconnect()
                    logger.info("exchange_disconnected", exchange=name)
                except Exception as e:
                    logger.warning("exchange_disconnect_failed", exchange=name, error=str(e))

            # Stop API server
            if self.api_server:
                await self.api_server.shutdown()
            logger.info("api_server_stopped")

            # Send shutdown notification
            # if self.alert_service:
            #     await self.alert_service.send_info("System Stopped", "Trading engine shut down gracefully")

        except Exception as e:
            logger.exception("shutdown_error", error=str(e))

        logger.info("application_stopped")

    async def run_forever(self) -> None:
        """Run until shutdown signal received."""
        await self._shutdown_event.wait()

    def trigger_shutdown(self) -> None:
        """Trigger graceful shutdown."""
        self._shutdown_event.set()


def setup_signal_handlers(app: Application, loop: asyncio.AbstractEventLoop) -> None:
    """Set up signal handlers for graceful shutdown."""

    def handle_signal(sig: signal.Signals) -> None:
        logger.info("shutdown_signal_received", signal=sig.name)
        app.trigger_shutdown()

    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, lambda s=sig: handle_signal(s))


async def async_main(config_path: str, password: Optional[str] = None) -> None:
    """Async main entry point."""

    # Load configuration
    try:
        config = load_config(config_path, password)
    except FileNotFoundError:
        logger.error("config_not_found", path=config_path)
        print(f"Error: Configuration file not found: {config_path}")
        print("Copy config/config.example.yaml to config/config.yaml and fill in your values.")
        sys.exit(1)
    except ValueError as e:
        logger.error("config_error", error=str(e))
        print(f"Error: {e}")
        sys.exit(1)

    # Set up logging based on config
    setup_logging(
        level="INFO" if config.is_simulation_mode() else "INFO",
        json_output=not config.is_simulation_mode(),
    )

    # Create and run application
    app = Application(config)

    # Set up signal handlers
    loop = asyncio.get_running_loop()
    setup_signal_handlers(app, loop)

    try:
        await app.start()
        await app.run_forever()
    finally:
        await app.stop()


def main() -> None:
    """Main entry point."""
    import argparse

    parser = argparse.ArgumentParser(description="Funding Rate Arbitrage Trading Engine")
    parser.add_argument(
        "-c", "--config",
        default="config/config.yaml",
        help="Path to configuration file (default: config/config.yaml)"
    )
    parser.add_argument(
        "-p", "--password",
        help="Master password for encrypted config (or set FUNDINGARB_MASTER_PASSWORD env)"
    )

    args = parser.parse_args()

    # Set up basic logging for startup
    setup_logging(level="INFO")

    logger.info("starting", config_path=args.config)

    try:
        asyncio.run(async_main(args.config, args.password))
    except KeyboardInterrupt:
        logger.info("keyboard_interrupt")
    except Exception as e:
        logger.exception("fatal_error", error=str(e))
        sys.exit(1)


if __name__ == "__main__":
    main()
