"""Migrations 0006 (sign-in links) and 0007 (question ballots), each on a throwaway
database of its own: additive on the way up, and a downgrade that refuses to destroy data
the previous schema cannot hold."""

from __future__ import annotations

import asyncio
import secrets
import uuid
from collections.abc import AsyncIterator

import asyncpg
import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker

from helpers import (
    TEST_DB_PREFIX,
    DatabaseInfo,
    Factory,
    create_database,
    database_named,
    drop_database,
    migrate,
)
from stagevote.config import get_settings
from stagevote.db import asyncpg_dsn, build_engine
from stagevote.security import new_token, token_hash

HEAD = "0007"

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
        assert await owner.fetchval("SELECT version_num FROM alembic_version") == HEAD
        assert len(await owner.fetch(LINK_COLUMNS)) == 4
    finally:
        await owner.close()


VOTE_COLUMNS = (
    "SELECT column_name, data_type, is_nullable FROM information_schema.columns "
    "WHERE table_name = 'votes' AND column_name IN ('nominee_id', 'answer_text') ORDER BY column_name"
)
TITLE_LENGTH = (
    "SELECT character_maximum_length FROM information_schema.columns "
    "WHERE table_name = 'ballots' AND column_name = 'title'"
)
INSERT_VOTE = (
    "INSERT INTO votes (id, receipt_code, programme_id, event_id, ballot_id, nominee_id, {extra}"
    "participant_id, session_id, idempotency_key, request_hash) "
    "VALUES ($1, $2, $3, $4, $5, $6, {extra_value}$7, $8, $9, 'hash')"
)


async def test_0007_adds_question_ballots_and_refuses_to_destroy_answers(scratch: DatabaseInfo) -> None:
    await alembic(scratch, "0006")
    engine = build_engine(get_settings(), scratch.owner_url, pool_size=1)
    factory = Factory(async_sessionmaker(engine, expire_on_commit=False, autoflush=False))
    owner = await connect(scratch.owner_url)
    app = await connect(scratch.app_url)
    try:
        # A vote recorded under the previous release.
        programme_id = await factory.create_programme()
        event = await factory.create_event(programme_id, name="Before")
        person = await factory.create_participant(programme_id)
        session_id = uuid.uuid4()
        await owner.execute(
            "INSERT INTO participant_sessions (id, participant_id, programme_id, token_hash, expires_at, user_agent) "
            "VALUES ($1, $2, $3, $4, now() + interval '1 hour', '')",
            session_id,
            person.id,
            programme_id,
            token_hash(new_token()),
        )
        old_vote = uuid.uuid4()
        await owner.execute(
            INSERT_VOTE.format(extra="", extra_value=""),
            old_vote,
            "R-OLDV-0001",
            programme_id,
            event.id,
            event.ballot.id,
            event.ballot.nominee_ids[0],
            person.id,
            session_id,
            "old-key",
        )

        await alembic(scratch, "0007")
        assert [tuple(c) for c in await owner.fetch(VOTE_COLUMNS)] == [
            ("answer_text", "text", "YES"),
            ("nominee_id", "uuid", "YES"),
        ]
        assert await owner.fetchval(TITLE_LENGTH) == 200
        row = await owner.fetchrow("SELECT nominee_id, answer_text FROM votes WHERE id = $1", old_vote)
        assert (row["nominee_id"], row["answer_text"]) == (event.ballot.nominee_ids[0], None)

        # A question ballot, or a title the old schema cannot hold, blocks the downgrade.
        question = uuid.uuid4()
        await owner.execute(
            "INSERT INTO ballots (id, programme_id, event_id, position, title, kind, description) "
            "VALUES ($1, $2, $3, 2, $4, 'mcq', '')",
            question,
            programme_id,
            event.id,
            "Q" * 200,
        )
        with pytest.raises(RuntimeError, match="refusing to downgrade"):
            await alembic(scratch, "0006", downgrade=True)
        await owner.execute("UPDATE ballots SET kind = 'team' WHERE id = $1", question)
        with pytest.raises(RuntimeError, match="refusing to downgrade"):
            await alembic(scratch, "0006", downgrade=True)
        assert await owner.fetchval("SELECT version_num FROM alembic_version") == "0007"

        # Without either, the downgrade restores the previous schema and keeps the old vote.
        await owner.execute("DELETE FROM ballots WHERE id = $1", question)
        await alembic(scratch, "0006", downgrade=True)
        assert await owner.fetchval("SELECT version_num FROM alembic_version") == "0006"
        assert [tuple(c) for c in await owner.fetch(VOTE_COLUMNS)] == [("nominee_id", "uuid", "NO")]
        assert await owner.fetchval(TITLE_LENGTH) == 80
        with pytest.raises(asyncpg.CheckViolationError):
            await owner.execute("UPDATE ballots SET kind = 'text' WHERE id = $1", event.ballot.id)
        assert await owner.fetchval("SELECT count(*) FROM votes WHERE id = $1", old_vote) == 1

        # Back up: the runtime role records a typed answer through its existing grants.
        await alembic(scratch, "head")
        await owner.execute(
            "INSERT INTO ballots (id, programme_id, event_id, position, title, kind, description) "
            "VALUES ($1, $2, $3, 2, 'Say something', 'text', '')",
            question,
            programme_id,
            event.id,
        )
        answer_vote = uuid.uuid4()
        typed = INSERT_VOTE.format(extra="answer_text, ", extra_value="$10, ")
        args = (programme_id, event.id, question, None, person.id, session_id)
        await app.execute(typed, answer_vote, "R-TEXT-0001", *args, "answer-key", "Hello")
        # Exactly one of a nominee and 1-500 characters of text.
        for code, nominee, answer in (
            ("R-TEXT-0002", None, ""),
            ("R-TEXT-0003", None, "x" * 501),
            ("R-TEXT-0004", event.ballot.nominee_ids[1], "both"),
        ):
            with pytest.raises(asyncpg.CheckViolationError):
                await app.execute(
                    typed,
                    uuid.uuid4(),
                    code,
                    programme_id,
                    event.id,
                    question,
                    nominee,
                    person.id,
                    session_id,
                    code,
                    answer,
                )
        with pytest.raises(asyncpg.CheckViolationError):
            await app.execute(
                INSERT_VOTE.format(extra="", extra_value=""),
                uuid.uuid4(),
                "R-TEXT-0005",
                *args,
                "no-choice",
            )
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            await app.execute("UPDATE votes SET answer_text = 'changed' WHERE id = $1", answer_vote)

        # A typed answer can never be removed, so the downgrade refuses for good.
        with pytest.raises(RuntimeError, match="refusing to downgrade"):
            await alembic(scratch, "0006", downgrade=True)
        assert await owner.fetchval("SELECT version_num FROM alembic_version") == HEAD
        assert await owner.fetchval("SELECT answer_text FROM votes WHERE id = $1", answer_vote) == "Hello"
    finally:
        await app.close()
        await owner.close()
        await engine.dispose()
