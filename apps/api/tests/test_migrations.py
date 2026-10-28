"""Migration 0006 (sign-in links), on a throwaway database of its own: additive on the way
up, and a downgrade that refuses to destroy issued links."""

from __future__ import annotations

import asyncio
import secrets
import uuid
from collections.abc import AsyncIterator

import asyncpg
import pytest

from helpers import (
    TEST_DB_PREFIX,
    DatabaseInfo,
    create_database,
    database_named,
    drop_database,
    migrate,
)
from stagevote.db import asyncpg_dsn
from stagevote.security import new_token, token_hash

LINK_COLUMNS = (
    "SELECT column_name, data_type, is_nullable FROM information_schema.columns "
    "WHERE table_name = 'participants' AND column_name LIKE 'link%' ORDER BY column_name"
)
OLD_HASH = "$argon2id$v=19$m=19456,t=2,p=1$from-the-previous-release"


@pytest.fixture
async def scratch(database: DatabaseInfo) -> AsyncIterator[DatabaseInfo]:
    db = database_named(database, f"{TEST_DB_PREFIX}{secrets.token_hex(6)}_migrations")
    await create_database(db)
    try:
        yield db
    finally:
        await drop_database(db)


async def alembic(db: DatabaseInfo, revision: str, *, downgrade: bool = False) -> None:
    # Alembic runs its own event loop, so it gets a thread of its own.
    await asyncio.to_thread(migrate, db.owner_url, revision, downgrade=downgrade)


async def connect(url: str) -> asyncpg.Connection:
    # No statement cache: the schema changes under this connection.
    return await asyncpg.connect(asyncpg_dsn(url), statement_cache_size=0)


async def test_0006_adds_sign_in_links_and_refuses_to_destroy_them(scratch: DatabaseInfo) -> None:
    await alembic(scratch, "0005")
    owner = await connect(scratch.owner_url)
    try:
        programme_id, person = uuid.uuid4(), uuid.uuid4()
        await owner.execute(
            "INSERT INTO programmes (id, slug, is_active, name, description, instructions, help_contact, "
            "accent, timezone, countdown_enabled, run_mode, schedule_suspended, schedule_revision, "
            "preview_upcoming, publish_schedule, state_version, live_version, settings_version) "
            "VALUES ($1, 'migration-test', true, 'Migration test', '', '', '', 'violet', 'Asia/Kolkata', "
            "true, 'manual', false, 1, false, true, 1, 1, 1)",
            programme_id,
        )
        # Someone who signed in with an emailed password under the previous release.
        await owner.execute(
            "INSERT INTO participants (id, programme_id, email, email_normalized, display_name, user_code, "
            "password_hash, credential_version, activated_at, synthetic) "
            "VALUES ($1, $2, 'before@example.com', 'before@example.com', 'Before Links', 'SV-MIGRATE1', "
            "$3, 1, now(), false)",
            person,
            programme_id,
            OLD_HASH,
        )

        await alembic(scratch, "0006")
        assert [tuple(c) for c in await owner.fetch(LINK_COLUMNS)] == [
            ("link_bound_at", "timestamp with time zone", "YES"),
            ("link_device_hash", "bytea", "YES"),
            ("link_issued_at", "timestamp with time zone", "YES"),
            ("link_token_hash", "bytea", "YES"),
        ]
        # Additive: existing rows and the retired password columns are untouched.
        row = await owner.fetchrow(
            "SELECT password_hash, activated_at, link_token_hash FROM participants WHERE id = $1", person
        )
        assert row["password_hash"] == OLD_HASH and row["activated_at"] is not None
        assert row["link_token_hash"] is None

        # The runtime role uses the new columns through its existing table grants, and a
        # participant may now be activated with a link alone.
        link = token_hash(new_token())
        app = await connect(scratch.app_url)
        try:
            await app.execute(
                "UPDATE participants SET link_token_hash = $1, link_issued_at = now(), password_hash = NULL "
                "WHERE id = $2",
                link,
                person,
            )
            assert (
                await app.fetchval("SELECT link_token_hash FROM participants WHERE id = $1", person) == link
            )
            with pytest.raises(asyncpg.CheckViolationError):  # but not with no credential at all
                await app.execute("UPDATE participants SET link_token_hash = NULL WHERE id = $1", person)
        finally:
            await app.close()

        # Links have been issued: the downgrade refuses before changing anything.
        with pytest.raises(RuntimeError, match="refusing to downgrade"):
            await alembic(scratch, "0005", downgrade=True)
        assert await owner.fetchval("SELECT version_num FROM alembic_version") == "0006"
        assert await owner.fetchval("SELECT link_token_hash FROM participants WHERE id = $1", person) == link

        # With no link issued, the downgrade restores the previous schema and its check.
        await owner.execute(
            "UPDATE participants SET link_token_hash = NULL, password_hash = $1 WHERE id = $2",
            OLD_HASH,
            person,
        )
        await alembic(scratch, "0005", downgrade=True)
        assert await owner.fetchval("SELECT version_num FROM alembic_version") == "0005"
        assert await owner.fetch(LINK_COLUMNS) == []
        with pytest.raises(asyncpg.CheckViolationError):
            await owner.execute("UPDATE participants SET password_hash = NULL WHERE id = $1", person)

        await alembic(scratch, "head")
        assert await owner.fetchval("SELECT version_num FROM alembic_version") == "0006"
        assert len(await owner.fetch(LINK_COLUMNS)) == 4
    finally:
        await owner.close()
