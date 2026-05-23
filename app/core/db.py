"""Async SQLAlchemy engine and session factory.

The DATABASE_URL env var is the Supabase Postgres Session-pooler connection
string. We rewrite the `postgresql://` scheme to `postgresql+asyncpg://` so
SQLAlchemy uses the asyncpg driver. The engine is created lazily and cached
so test code can override settings before the first call.
"""

from collections.abc import AsyncIterator
from functools import lru_cache

from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core.config import get_settings


@lru_cache
def _engine():
    settings = get_settings()
    return create_async_engine(
        settings.database_url_async,
        pool_pre_ping=True,
        future=True,
    )


@lru_cache
def _sessionmaker() -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(_engine(), expire_on_commit=False, class_=AsyncSession)


async def get_db() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency yielding an `AsyncSession` per request."""
    async with _sessionmaker()() as session:
        yield session
