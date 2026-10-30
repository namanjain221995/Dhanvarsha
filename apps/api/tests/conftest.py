"""Session fixtures: a throwaway PostgreSQL database, the ASGI app and per-test data.

The suite never touches the development database. It creates `stagevote_test_<random>`
owned by `stagevote_owner` (schema prepared like infra/postgres/init), migrates it with
Alembic as the owner, runs the app as the restricted runtime role `stagevote_app` and
drops the database when the session ends.

Connection settings come from `scripts/host-env.sh` (`TEST_SUPERUSER_URL`,
`DB_OWNER_PASSWORD`, `DB_APP_PASSWORD`). If they are not exported, the script is sourced
automatically so `uv run pytest` works from a plain shell.
"""

from __future__ import annotations

import asyncio
import os
import secrets
import subprocess
import tempfile
from collections.abc import AsyncIterator, Iterator
from dataclasses import dataclass
from typing import Any

import pytest
from cryptography.fernet import Fernet
from fastapi import FastAPI
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from helpers import (
    APP_ROLE,
    OWNER_ROLE,
    REPO_ROOT,
    TEST_DB_PREFIX,
    DatabaseInfo,
    Factory,
    World,
    create_database,
    drop_database,
    in_thread,
    migrate,
)
from stagevote.config import get_settings
from stagevote.db import build_engine, dispose_engine, set_engine
from stagevote.mailer.providers import RecordingProvider
from stagevote.main import create_app
from stagevote.worker.runner import Worker

TEST_DB_NAME = f"{TEST_DB_PREFIX}{secrets.token_hex(6)}"


def _load_host_env() -> None:
    """Import the database credentials from scripts/host-env.sh when not exported."""
    needed = ("TEST_SUPERUSER_URL", "DB_OWNER_PASSWORD", "DB_APP_PASSWORD")
    if all(os.environ.get(k) for k in needed):
        return
    script = REPO_ROOT / "scripts" / "host-env.sh"
    if not script.exists():
        return
    result = subprocess.run(
        ["/bin/sh", "-c", '. "$1" >/dev/null 2>&1 && env -0', "sh", str(script)],
        cwd=REPO_ROOT,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        return
    for item in result.stdout.split(b"\0"):
        key, _, value = item.decode(errors="replace").partition("=")
        if key in (*needed, "MAILPIT_SMTP_PORT", "MAILPIT_UI_PORT") and not os.environ.get(key):
            os.environ[key] = value


def _database_info() -> DatabaseInfo | None:
    superuser = os.environ.get("TEST_SUPERUSER_URL")
    owner_pw = os.environ.get("DB_OWNER_PASSWORD")
    app_pw = os.environ.get("DB_APP_PASSWORD")
    if not (superuser and owner_pw and app_pw):
        return None
    base = make_url(superuser)

    def render(**parts: Any) -> str:
        return base.set(**parts).render_as_string(hide_password=False)

    return DatabaseInfo(
        name=TEST_DB_NAME,
        owner_url=render(username=OWNER_ROLE, password=owner_pw, database=TEST_DB_NAME),
        app_url=render(username=APP_ROLE, password=app_pw, database=TEST_DB_NAME),
        super_url=render(database=TEST_DB_NAME),
        super_admin_url=render(database="postgres"),
    )


_load_host_env()
DB = _database_info()
MAILPIT_SMTP_PORT = os.environ.get("MAILPIT_SMTP_PORT") or "1025"

# Deterministic settings for every test, whatever the calling shell exported. The app
# always points at the throwaway database (never the development database).
TEST_ENV = {
    "APP_ENV": "test",
    "APP_BASE_URL": "http://testserver",
    "EXTRA_ALLOWED_ORIGINS": "",
    "APP_SECRET_KEY": secrets.token_urlsafe(48),
    "INVITATION_ENCRYPTION_KEY": Fernet.generate_key().decode(),
    "DATABASE_URL": DB.app_url if DB else "postgresql+asyncpg://unset:unset@127.0.0.1:9/stagevote_test_unset",
    "DATABASE_MIGRATION_URL": DB.owner_url if DB else "",
    "DB_APP_ROLE": APP_ROLE,
    "DB_POOL_SIZE": "20",
    "DB_MAX_OVERFLOW": "10",
    "VOTE_LOCK_TIMEOUT_MS": "5000",
    "EMAIL_PROVIDER": "mailpit",
    "GOOGLE_SERVICE_ACCOUNT_FILE": "",
    "GOOGLE_WORKSPACE_SENDER": "",
    "MAIL_FROM": "StageVote Tests <noreply@example.com>",
    "MAIL_REPLY_TO": "",
    "SMTP_HOST": "127.0.0.1",
    "SMTP_PORT": MAILPIT_SMTP_PORT,
    "SMTP_USERNAME": "",
    "SMTP_PASSWORD": "",
    "SMTP_TLS_MODE": "none",
    "SMTP_TIMEOUT_SECONDS": "10",
    "EMAIL_MAX_ATTEMPTS": "6",
    "EMAIL_CONCURRENCY": "4",
    "EMAIL_MAX_PER_MINUTE": "1000",
    "SESSION_TTL_HOURS": "12",
    "DEVICE_COOKIE_DAYS": "180",
    "INVITATION_RETRY_HOURS": "72",
    "LOGIN_FAILURES_PER_IP": "300",
    "LOGIN_IP_WINDOW_SECONDS": "600",
    "PROGRAMME_TIMEZONE": "Asia/Kolkata",
    "MEDIA_ROOT": tempfile.mkdtemp(prefix="stagevote-test-media-"),
    "MAX_UPLOAD_BYTES": str(5 * 1024 * 1024),
    "MAX_MEDIA_BYTES": str(1024 * 1024 * 1024),
    "MAX_MEDIA_IMAGE_BYTES": str(40 * 1024 * 1024),
    "LOG_LEVEL": "INFO",
}
os.environ.update(TEST_ENV)
os.environ.pop("WORKER_ID", None)
get_settings.cache_clear()


# ---- database lifecycle -----------------------------------------------------------


@pytest.fixture(scope="session")
def database() -> Iterator[DatabaseInfo]:
    if DB is None:
        raise RuntimeError(
            "PostgreSQL test credentials are missing. Run `. scripts/host-env.sh` from the repository "
            "root (it exports TEST_SUPERUSER_URL, DB_OWNER_PASSWORD and DB_APP_PASSWORD)."
        )
    assert TEST_DB_NAME in get_settings().database_url, "refusing to run against a non-test database"
    in_thread(asyncio.run, create_database(DB))
    try:
        in_thread(migrate, DB.owner_url)
        yield DB
    finally:
        in_thread(asyncio.run, drop_database(DB))


@dataclass
class Engines:
    runtime: AsyncEngine
    owner: AsyncEngine
    owner_maker: async_sessionmaker[AsyncSession]


@pytest.fixture(scope="session")
async def engines(database: DatabaseInfo) -> AsyncIterator[Engines]:
    settings = get_settings()
    runtime = build_engine(settings, database.app_url)
    owner = build_engine(settings, database.owner_url, pool_size=4)
    # The app, the worker and the services all use the runtime role, as in production.
    set_engine(runtime)
    try:
        yield Engines(runtime, owner, async_sessionmaker(owner, expire_on_commit=False, autoflush=False))
    finally:
        await dispose_engine()
        await owner.dispose()


@pytest.fixture(scope="session")
async def app(engines: Engines) -> FastAPI:
    # httpx.ASGITransport does not run the lifespan: the engine was installed above and
    # the WebSocket hub is exercised by the multi-process tests instead.
    return create_app(start_hub=False)


@pytest.fixture(scope="session")
def factory(engines: Engines) -> Factory:
    return Factory(engines.owner_maker)


@pytest.fixture
async def world(app: FastAPI, factory: Factory) -> AsyncIterator[World]:
    programme_id = await factory.create_programme()
    w = World(app, factory, programme_id)
    try:
        yield w
    finally:
        await w.aclose()
        await factory.retire_programme(programme_id)


@pytest.fixture
def provider() -> RecordingProvider:
    return RecordingProvider()


@pytest.fixture
async def worker(engines: Engines, provider: RecordingProvider) -> AsyncIterator[Worker]:
    w = Worker(get_settings(), provider=provider)
    try:
        yield w
    finally:
        for task in list(w.in_flight):
            task.cancel()
        await asyncio.gather(*w.in_flight, return_exceptions=True)
