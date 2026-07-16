from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from nodeskclaw_rpa_engine.core.config import Settings


class DatabaseConfigurationError(ValueError):
    pass


class DatabaseManager:
    """Owns the Engine pool without creating, migrating, or seeding any tables."""

    def __init__(
        self,
        database_url: str,
        *,
        schema: str,
        pool_size: int,
        app_name: str,
    ) -> None:
        if not database_url.startswith("postgresql+asyncpg://"):
            raise DatabaseConfigurationError(
                "DATABASE_URL must use the postgresql+asyncpg driver"
            )
        if schema != "rpa_engine":
            raise DatabaseConfigurationError("Only the rpa_engine schema is allowed")

        self._engine: AsyncEngine = create_async_engine(
            database_url,
            pool_pre_ping=True,
            pool_size=pool_size,
            connect_args={
                "server_settings": {
                    "search_path": f"{schema},public",
                    "application_name": app_name,
                }
            },
        )
        self._session_factory = async_sessionmaker(
            self._engine,
            class_=AsyncSession,
            expire_on_commit=False,
        )

    @classmethod
    def from_settings(cls, settings: Settings) -> DatabaseManager:
        if settings.database_url is None:
            raise DatabaseConfigurationError("DATABASE_URL is not configured")
        return cls(
            settings.database_url.get_secret_value(),
            schema=settings.database_schema,
            pool_size=settings.database_pool_size,
            app_name=settings.app_name,
        )

    @asynccontextmanager
    async def session(self) -> AsyncIterator[AsyncSession]:
        async with self._session_factory() as session:
            try:
                yield session
            except Exception:
                await session.rollback()
                raise

    async def check(self) -> None:
        """Run a read-only liveness query only when DB readiness is enabled."""
        async with self._engine.connect() as connection:
            await connection.execute(text("SELECT 1"))

    async def close(self) -> None:
        await self._engine.dispose()
