"""Database engine and session helpers."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from .config import Settings, get_settings

_engine: AsyncEngine | None = None
_sessionmaker: async_sessionmaker[AsyncSession] | None = None


def build_engine(settings: Settings, url: str | None = None, *, pool_size: int | None = None) -> AsyncEngine:
    return create_async_engine(
        url or settings.database_url,
        pool_size=pool_size or settings.db_pool_size,
        max_overflow=settings.db_max_overflow,
        pool_pre_ping=True,
        pool_recycle=1800,
        # Errors must not log statement parameters (emails, password hashes).
        hide_parameters=True,
        connect_args={
            "server_settings": {
                "application_name": "stagevote",
                "statement_timeout": str(settings.db_statement_timeout_ms),
                "idle_in_transaction_session_timeout": "60000",
                "timezone": "UTC",
            },
        },
    )


def init_engine(settings: Settings | None = None) -> AsyncEngine:
    global _engine, _sessionmaker
    if _engine is None:
        settings = settings or get_settings()
        _engine = build_engine(settings)
        _sessionmaker = async_sessionmaker(_engine, expire_on_commit=False, autoflush=False)
    return _engine


def set_engine(engine: AsyncEngine) -> None:
    """Install an externally created engine (tests)."""
    global _engine, _sessionmaker
    _engine = engine
    _sessionmaker = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)


def get_engine() -> AsyncEngine:
    if _engine is None:
        return init_engine()
    return _engine


def sessionmaker() -> async_sessionmaker[AsyncSession]:
    if _sessionmaker is None:
        init_engine()
    assert _sessionmaker is not None
    return _sessionmaker


async def dispose_engine() -> None:
    global _engine, _sessionmaker
    if _engine is not None:
        await _engine.dispose()
    _engine = None
    _sessionmaker = None


@asynccontextmanager
async def session_scope() -> AsyncIterator[AsyncSession]:
    """A session whose transaction commits on success and rolls back on error."""
    async with sessionmaker()() as session, session.begin():
        yield session


async def get_db() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency: a session without an open transaction.

    Endpoints open explicit transactions (`async with db.begin()`) so that the commit
    point of every write is visible in the code.
    """
    async with sessionmaker()() as session:
        yield session


def asyncpg_dsn(url: str) -> str:
    """Convert a SQLAlchemy URL into a plain asyncpg DSN (for LISTEN connections)."""
    return url.replace("postgresql+asyncpg://", "postgresql://", 1)
