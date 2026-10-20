"""Alembic environment: runs migrations with the owner role (DATABASE_MIGRATION_URL)."""

from __future__ import annotations

import asyncio
import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import create_async_engine

from stagevote.models import Base

config = context.config
if config.config_file_name is not None and not config.attributes.get("skip_logging_config"):
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def _url() -> str:
    url = (
        config.attributes.get("url")
        or os.environ.get("DATABASE_MIGRATION_URL")
        or os.environ.get("DATABASE_URL")
    )
    if not url:
        raise RuntimeError("DATABASE_MIGRATION_URL is not set")
    return url


def _app_role() -> str:
    return config.attributes.get("app_role") or os.environ.get("DB_APP_ROLE", "stagevote_app")


def run_migrations_offline() -> None:
    context.configure(url=_url(), target_metadata=target_metadata, literal_binds=True, app_role=_app_role())
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    context.configure(
        connection=connection, target_metadata=target_metadata, compare_type=True, app_role=_app_role()
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    engine = create_async_engine(_url())
    async with engine.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    existing = config.attributes.get("connection")
    if existing is not None:
        do_run_migrations(existing)
    else:
        asyncio.run(run_async_migrations())
