"""
FastAPI application server.
"""

import asyncio
from contextlib import asynccontextmanager
from typing import Optional

import uvicorn
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

from ..config.schema import Config, APIConfig
from ..utils.logging import get_logger
from .routes import positions_router, engine_router, config_router, health_router
from .websocket import ws_manager

logger = get_logger(__name__)


def create_app(
    config: Optional[Config] = None,
    coordinator = None,
    exchanges: dict = None,
) -> FastAPI:
    """
    Create and configure the FastAPI application.

    Args:
        config: Application configuration
        coordinator: Trading coordinator instance
        exchanges: Connected exchange adapters

    Returns:
        Configured FastAPI application
    """

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        """Application lifespan management."""
        logger.info("api_server_starting")

        # Store references in app state
        app.state.config = config
        app.state.coordinator = coordinator
        app.state.exchanges = exchanges or {}

        yield

        logger.info("api_server_stopping")

    app = FastAPI(
        title="Funding Rate Arbitrage API",
        description="API for the Funding Rate Arbitrage Trading System",
        version="1.0.0",
        lifespan=lifespan,
    )

    # Configure CORS
    cors_origins = ["*"]
    if config and config.api:
        cors_origins = config.api.cors_origins

    app.add_middleware(
        CORSMiddleware,
        allow_origins=cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Include routers
    app.include_router(health_router, prefix="/api", tags=["Health"])
    app.include_router(positions_router, prefix="/api/positions", tags=["Positions"])
    app.include_router(engine_router, prefix="/api/engine", tags=["Engine"])
    app.include_router(config_router, prefix="/api/config", tags=["Configuration"])

    # WebSocket endpoint
    @app.websocket("/ws")
    async def websocket_endpoint(websocket: WebSocket):
        """WebSocket endpoint for real-time updates."""
        await ws_manager.connect(websocket)
        try:
            while True:
                # Keep connection alive, receive any client messages
                data = await websocket.receive_text()
                # Could handle client commands here if needed
                logger.debug("websocket_message_received", data=data)
        except WebSocketDisconnect:
            ws_manager.disconnect(websocket)
        except Exception as e:
            logger.warning("websocket_error", error=str(e))
            ws_manager.disconnect(websocket)

    # Root endpoint
    @app.get("/")
    async def root():
        """Root endpoint."""
        return {
            "name": "Funding Rate Arbitrage API",
            "version": "1.0.0",
            "docs": "/docs",
            "health": "/api/health",
        }

    return app


async def run_server(
    app: FastAPI,
    config: Optional[APIConfig] = None,
) -> None:
    """
    Run the API server.

    Args:
        app: FastAPI application
        config: API configuration
    """
    host = config.host if config else "0.0.0.0"
    port = config.port if config else 8000

    logger.info("starting_uvicorn", host=host, port=port)

    server_config = uvicorn.Config(
        app=app,
        host=host,
        port=port,
        log_level="info",
        access_log=True,
    )

    server = uvicorn.Server(server_config)
    await server.serve()


def run_server_sync(
    config_path: str = "config/config.yaml",
    host: str = "0.0.0.0",
    port: int = 8000,
) -> None:
    """
    Run the API server synchronously (for CLI usage).

    Args:
        config_path: Path to configuration file
        host: Host to bind to
        port: Port to bind to
    """
    from ..config import load_config

    # Load config
    try:
        config = load_config(config_path)
    except FileNotFoundError:
        config = None
        logger.warning("config_not_found", path=config_path)

    app = create_app(config)

    uvicorn.run(
        app,
        host=host,
        port=port,
        log_level="info",
    )
