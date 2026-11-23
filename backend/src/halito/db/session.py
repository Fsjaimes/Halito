"""Motor y sesiones asíncronas de SQLAlchemy."""

from collections.abc import AsyncIterator
from functools import lru_cache

from sqlalchemy import Engine, create_engine
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from halito.config import get_settings


@lru_cache
def get_engine() -> AsyncEngine:
    return create_async_engine(get_settings().database_url, pool_pre_ping=True)


@lru_cache
def get_sessionmaker() -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(get_engine(), expire_on_commit=False)


async def get_session() -> AsyncIterator[AsyncSession]:
    """Dependencia de FastAPI: una sesión por petición."""
    async with get_sessionmaker()() as session:
        yield session


@lru_cache
def get_sync_engine() -> Engine:
    """Motor síncrono para la ingesta, que corre como proceso por lotes."""
    return create_engine(get_settings().database_url, pool_pre_ping=True)
