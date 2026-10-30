"""Migrations 0006 (sign-in links), 0007 (question ballots), 0008 (the opening page) and
0009 (opening-page media), each on a throwaway database of its own: additive on the way
up, and a downgrade that refuses to destroy data the previous schema cannot hold."""

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

HEAD = "0009"

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


async def old_programme(owner: asyncpg.Connection, name: str) -> uuid.UUID:
    """A programme made under an earlier release. It is inserted with SQL and only the
    columns every revision has, since the ORM model always writes the newest columns."""
    programme_id = uuid.uuid4()
    await owner.execute(
        "INSERT INTO programmes (id, slug, is_active, name, description, instructions, help_contact, "
        "accent, timezone, countdown_enabled, run_mode, schedule_suspended, schedule_revision, "
        "preview_upcoming, publish_schedule, state_version, live_version, settings_version) "
        "VALUES ($1, $2, true, $3, '', '', '', 'violet', 'Asia/Kolkata', "
        "true, 'manual', false, 1, false, true, 1, 1, 1)",
        programme_id,
        f"migration-{programme_id.hex[:12]}",
        name,
    )
    return programme_id


async def test_0006_adds_sign_in_links_and_refuses_to_destroy_them(scratch: DatabaseInfo) -> None:
    await alembic(scratch, "0005")
    owner = await connect(scratch.owner_url)
    try:
        programme_id, person = await old_programme(owner, "Migration test"), uuid.uuid4()
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
        programme_id = await old_programme(owner, "Before questions")
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


OPENING_COLUMN = (
    "SELECT data_type, is_nullable, column_default FROM information_schema.columns "
    "WHERE table_name = 'programmes' AND column_name = 'opening'"
)


async def test_0008_adds_the_opening_page_and_drops_it_on_downgrade(scratch: DatabaseInfo) -> None:
    await alembic(scratch, "0007")
    engine = build_engine(get_settings(), scratch.owner_url, pool_size=1)
    factory = Factory(async_sessionmaker(engine, expire_on_commit=False, autoflush=False))
    owner = await connect(scratch.owner_url)
    app = await connect(scratch.app_url)
    try:
        # A programme set up under the previous release.
        programme_id = await old_programme(owner, "Dhanvarsha 2026")

        await alembic(scratch, "0008")
        assert tuple(await owner.fetchrow(OPENING_COLUMN)) == ("jsonb", "NO", "'{}'::jsonb")
        # Every existing programme shows the built-in page.
        assert (
            await owner.fetchval("SELECT opening::text FROM programmes WHERE id = $1", programme_id) == "{}"
        )

        # The runtime role saves a page through its existing table grants; only an object fits.
        await app.execute(
            "UPDATE programmes SET opening = $1::jsonb WHERE id = $2",
            '{"button_label": "PLAY"}',
            programme_id,
        )
        with pytest.raises(asyncpg.CheckViolationError):
            await app.execute("UPDATE programmes SET opening = '[]'::jsonb WHERE id = $1", programme_id)
        # Programmes created by the application get the empty page too.
        created = await factory.create_programme(activate=False)
        assert await owner.fetchval("SELECT opening::text FROM programmes WHERE id = $1", created) == "{}"

        await alembic(scratch, "0007", downgrade=True)
        assert await owner.fetchval("SELECT version_num FROM alembic_version") == "0007"
        assert await owner.fetchrow(OPENING_COLUMN) is None
        assert (
            await owner.fetchval("SELECT name FROM programmes WHERE id = $1", programme_id)
            == "Dhanvarsha 2026"
        )

        await alembic(scratch, "head")
        assert await owner.fetchval("SELECT version_num FROM alembic_version") == HEAD
        assert (
            await owner.fetchval("SELECT opening::text FROM programmes WHERE id = $1", programme_id) == "{}"
        )
    finally:
        await app.close()
        await owner.close()
        await engine.dispose()


ASSET_NAME_COLUMN = (
    "SELECT data_type, character_maximum_length, is_nullable FROM information_schema.columns "
    "WHERE table_name = 'assets' AND column_name = 'original_name'"
)
ASSET_KIND_CHECKS = (
    "SELECT conname FROM pg_constraint WHERE conrelid = 'assets'::regclass AND contype = 'c' ORDER BY conname"
)
INSERT_ASSET = (
    "INSERT INTO assets (id, kind, storage_key, content_type, width, height, byte_size, sha256{extra}) "
    "VALUES ($1, $2, $3, $4, 0, 0, 10, repeat('0', 64){extra_value})"
)


async def test_0009_adds_opening_media_and_refuses_to_drop_it(scratch: DatabaseInfo) -> None:
    await alembic(scratch, "0008")
    owner = await connect(scratch.owner_url)
    app = await connect(scratch.app_url)
    try:
        # A picture uploaded under the previous release; the new kind does not fit yet.
        picture = uuid.uuid4()
        await owner.execute(
            INSERT_ASSET.format(extra="", extra_value=""),
            picture,
            "event_logo",
            f"{picture.hex}.webp",
            "image/webp",
        )
        assert [r["conname"] for r in await owner.fetch(ASSET_KIND_CHECKS)] == ["assets_kind_check"]
        with pytest.raises(asyncpg.CheckViolationError):
            await owner.execute(
                INSERT_ASSET.format(extra="", extra_value=""),
                uuid.uuid4(),
                "opening_media",
                f"{uuid.uuid4().hex}.mp4",
                "video/mp4",
            )

        await alembic(scratch, "0009")
        assert tuple(await owner.fetchrow(ASSET_NAME_COLUMN)) == ("character varying", 255, "YES")
        assert [r["conname"] for r in await owner.fetch(ASSET_KIND_CHECKS)] == ["ck_assets_kind"]
        # Additive: the old picture is untouched and has no file name.
        row = await owner.fetchrow("SELECT kind, original_name FROM assets WHERE id = $1", picture)
        assert (row["kind"], row["original_name"]) == ("event_logo", None)

        # The runtime role stores a video through its existing table grants; unknown kinds still fail.
        video = uuid.uuid4()
        named = INSERT_ASSET.format(extra=", original_name", extra_value=", $5")
        await app.execute(named, video, "opening_media", f"{video.hex}.mp4", "video/mp4", "Opening film.mp4")
        assert (
            await app.fetchval("SELECT original_name FROM assets WHERE id = $1", video) == "Opening film.mp4"
        )
        with pytest.raises(asyncpg.CheckViolationError):
            await app.execute(named, uuid.uuid4(), "poster", f"{uuid.uuid4().hex}.webp", "image/webp", "x")

        # A video the page may show blocks the downgrade, which then changes nothing.
        with pytest.raises(RuntimeError, match="refusing to downgrade"):
            await alembic(scratch, "0008", downgrade=True)
        assert await owner.fetchval("SELECT version_num FROM alembic_version") == "0009"
        assert await owner.fetchval("SELECT count(*) FROM assets WHERE id = $1", video) == 1

        # Without one, the downgrade restores the previous schema and its check.
        await owner.execute("DELETE FROM assets WHERE id = $1", video)
        await alembic(scratch, "0008", downgrade=True)
        assert await owner.fetchval("SELECT version_num FROM alembic_version") == "0008"
        assert await owner.fetchrow(ASSET_NAME_COLUMN) is None
        assert [r["conname"] for r in await owner.fetch(ASSET_KIND_CHECKS)] == ["assets_kind_check"]
        assert await owner.fetchval("SELECT kind FROM assets WHERE id = $1", picture) == "event_logo"

        await alembic(scratch, "head")
        assert await owner.fetchval("SELECT version_num FROM alembic_version") == HEAD
        assert [r["conname"] for r in await owner.fetch(ASSET_KIND_CHECKS)] == ["ck_assets_kind"]
    finally:
        await app.close()
        await owner.close()
