"""
Database connection management with async SQLAlchemy.

Supports both SQLite (development) and PostgreSQL (production).
"""

from contextlib import asynccontextmanager
from typing import AsyncGenerator, Optional

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool, AsyncAdaptedQueuePool

from ..config.schema import DatabaseConfig
from ..utils.logging import get_logger
from .models import Base

logger = get_logger(__name__)


class DatabaseSessionManager:
    """
    Manages database connections and sessions.

    Usage:
        manager = DatabaseSessionManager()
        await manager.init(config)

        async with manager.session() as session:
            # Use session
            pass

        await manager.close()
    """

    def __init__(self):
        self._engine: Optional[AsyncEngine] = None
        self._sessionmaker: Optional[async_sessionmaker[AsyncSession]] = None

    async def init(self, config: DatabaseConfig) -> None:
        """
        Initialize database engine and create tables.

        Args:
            config: Database configuration
        """
        connection_url = config.get_connection_url()

        # Configure pool based on driver
        if config.driver == "sqlite":
            # SQLite doesn't support connection pooling well with async
            pool_class = NullPool
            pool_kwargs = {}
        else:
            pool_class = AsyncAdaptedQueuePool
            pool_kwargs = {
                "pool_size": config.pool_size,
                "max_overflow": config.max_overflow,
                "pool_pre_ping": True,  # Enable connection health checks
            }

        self._engine = create_async_engine(
            connection_url,
            poolclass=pool_class,
            echo=False,  # Set to True for SQL debugging
            **pool_kwargs,
        )

        self._sessionmaker = async_sessionmaker(
            bind=self._engine,
            class_=AsyncSession,
            expire_on_commit=False,
            autoflush=False,
        )

        # Create tables
        async with self._engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        logger.info(
            "database_initialized",
            driver=config.driver,
            pool_size=config.pool_size if config.driver != "sqlite" else "N/A",
        )

    async def close(self) -> None:
        """Close all database connections."""
        if self._engine:
            await self._engine.dispose()
            self._engine = None
            self._sessionmaker = None
            logger.info("database_closed")

    @asynccontextmanager
    async def session(self) -> AsyncGenerator[AsyncSession, None]:
        """
        Get a database session as a context manager.

        Usage:
            async with manager.session() as session:
                result = await session.execute(query)
        """
        if self._sessionmaker is None:
            raise RuntimeError("Database not initialized. Call init() first.")

        session = self._sessionmaker()
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()

    @property
    def engine(self) -> AsyncEngine:
        """Get the database engine."""
        if self._engine is None:
            raise RuntimeError("Database not initialized. Call init() first.")
        return self._engine


# Global session manager instance
_session_manager: Optional[DatabaseSessionManager] = None


async def init_database(config: DatabaseConfig) -> DatabaseSessionManager:
    """
    Initialize the global database session manager.

    Args:
        config: Database configuration

    Returns:
        Initialized session manager
    """
    global _session_manager
    _session_manager = DatabaseSessionManager()
    await _session_manager.init(config)
    return _session_manager


async def close_database() -> None:
    """Close the global database connection."""
    global _session_manager
    if _session_manager:
        await _session_manager.close()
        _session_manager = None


def get_session_manager() -> DatabaseSessionManager:
    """Get the global session manager."""
    if _session_manager is None:
        raise RuntimeError("Database not initialized. Call init_database() first.")
    return _session_manager


@asynccontextmanager
async def get_session() -> AsyncGenerator[AsyncSession, None]:
    """
    Get a database session from the global manager.

    Usage:
        async with get_session() as session:
            result = await session.execute(query)
    """
    async with get_session_manager().session() as session:
        yield session


def get_engine() -> AsyncEngine:
    """Get the database engine from the global manager."""
    return get_session_manager().engine
