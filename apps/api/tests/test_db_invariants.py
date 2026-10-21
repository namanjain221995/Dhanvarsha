"""Invariants PostgreSQL enforces on its own, whatever the application code does.

These are the last line of defence: the runtime role cannot rewrite history, the
immutability trigger stops even the owner, and partial unique indexes and composite
foreign keys make impossible states unrepresentable.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, IntegrityError

from helpers import Factory, World, pg_error
from stagevote.db import sessionmaker

INSUFFICIENT_PRIVILEGE = "42501"
UNIQUE_VIOLATION = "23505"
FOREIGN_KEY_VIOLATION = "23503"
VOTES_IMMUTABLE = "SV001"


async def _cast_vote(world: World) -> tuple[uuid.UUID, uuid.UUID]:
    """A real vote through the API; returns (vote id, participant id)."""
    event = await world.live_event()
    voter = await world.participant()
    resp = await voter.vote(event.ballot.id, event.ballot.nominee_ids[0])
    assert resp.status_code == 201, resp.text
    vote_id = await world.factory.fetchval("SELECT id FROM votes WHERE participant_id = :p", p=voter.info.id)
    return vote_id, voter.info.id


@pytest.mark.parametrize(
    "statement",
    [
        "UPDATE votes SET nominee_id = nominee_id WHERE id = :id",
        "DELETE FROM votes WHERE id = :id",
        "TRUNCATE votes",
        "UPDATE audit_log SET action = 'tampered' WHERE id > 0",
        "DELETE FROM audit_log WHERE id > 0",
    ],
)
async def test_runtime_role_cannot_rewrite_votes_or_audit_log(world: World, statement: str) -> None:
    vote_id, _ = await _cast_vote(world)
    async with sessionmaker()() as db:  # the application's runtime role
        with pytest.raises(DBAPIError) as caught:
            async with db.begin():
                await db.execute(text(statement), {"id": vote_id} if ":id" in statement else {})
    assert pg_error(caught.value).sqlstate == INSUFFICIENT_PRIVILEGE
    assert await world.factory.fetchval("SELECT count(*) FROM votes WHERE id = :id", id=vote_id) == 1


@pytest.mark.parametrize(
    "statement",
    [
        "UPDATE votes SET nominee_id = nominee_id WHERE id = :id",
        "DELETE FROM votes WHERE id = :id",
        "TRUNCATE votes",
    ],
)
async def test_immutability_trigger_blocks_even_the_owner(
    world: World, factory: Factory, statement: str
) -> None:
    vote_id, _ = await _cast_vote(world)
    with pytest.raises(DBAPIError) as caught:
        await factory.execute(statement, **({"id": vote_id} if ":id" in statement else {}))
    error = pg_error(caught.value)
    assert error.sqlstate == VOTES_IMMUTABLE
    assert "votes are immutable" in str(error)
    assert await factory.fetchval("SELECT count(*) FROM votes WHERE id = :id", id=vote_id) == 1


async def test_at_most_one_open_event_per_programme(world: World, factory: Factory) -> None:
    await world.live_event()
    second = await world.event()
    with pytest.raises(IntegrityError) as caught:
        await factory.execute(
            "UPDATE events SET status = 'open', opened_at = clock_timestamp(), "
            "voting_deadline_at = clock_timestamp() + interval '10 minutes' WHERE id = :id",
            id=second.id,
        )
    error = pg_error(caught.value)
    assert error.sqlstate == UNIQUE_VIOLATION
    assert error.constraint_name == "uq_events_one_open_per_programme"


async def test_several_live_sessions_per_participant_but_tokens_stay_unique(
    world: World, factory: Factory
) -> None:
    voter = await world.participant()
    insert = (
        "INSERT INTO participant_sessions (id, participant_id, programme_id, token_hash, expires_at, user_agent) "
        "VALUES (:id, :p, :prog, :h, clock_timestamp() + interval '1 hour', 'second device')"
    )
    # A second device signed in at the same time is allowed (migration 0005).
    second_hash = uuid.uuid4().bytes * 2
    await factory.execute(insert, id=uuid.uuid4(), p=voter.info.id, prog=world.programme_id, h=second_hash)
    live = await factory.fetchval(
        "SELECT count(*) FROM participant_sessions WHERE participant_id = :p AND revoked_at IS NULL",
        p=voter.info.id,
    )
    assert live == 2
    # Two sessions can never share a token.
    with pytest.raises(IntegrityError) as caught:
        await factory.execute(
            insert, id=uuid.uuid4(), p=voter.info.id, prog=world.programme_id, h=second_hash
        )
    assert pg_error(caught.value).sqlstate == UNIQUE_VIOLATION


async def test_vote_rows_must_be_internally_consistent(world: World, factory: Factory) -> None:
    """Composite foreign keys reject a vote whose parts do not belong together."""
    event = await world.live_event(ballots=2)
    voter = await world.participant()
    other = await world.participant()
    session_id = await factory.fetchval(
        "SELECT id FROM participant_sessions WHERE participant_id = :p AND revoked_at IS NULL",
        p=voter.info.id,
    )
    other_session = await factory.fetchval(
        "SELECT id FROM participant_sessions WHERE participant_id = :p AND revoked_at IS NULL",
        p=other.info.id,
    )
    first, second = event.ballots
    base = {
        "prog": world.programme_id,
        "eid": event.id,
        "bid": first.id,
        "nid": first.nominee_ids[0],
        "pid": voter.info.id,
        "sid": session_id,
    }
    cases = {
        "fk_votes_nominee_in_ballot": {"nid": second.nominee_ids[0]},
        "fk_votes_session_of_participant": {"sid": other_session},
    }
    for constraint, override in cases.items():
        params = base | override
        async with sessionmaker()() as db:  # runtime role, bypassing the service checks
            with pytest.raises(IntegrityError) as caught:
                async with db.begin():
                    await db.execute(
                        text(
                            "INSERT INTO votes (id, receipt_code, programme_id, event_id, ballot_id, nominee_id, "
                            "participant_id, session_id, idempotency_key, request_hash) VALUES (:id, :code, :prog, "
                            ":eid, :bid, :nid, :pid, :sid, :key, 'x')"
                        ),
                        params
                        | {"id": uuid.uuid4(), "code": f"T-{uuid.uuid4().hex[:8]}", "key": uuid.uuid4().hex},
                    )
        error = pg_error(caught.value)
        assert error.sqlstate == FOREIGN_KEY_VIOLATION
        assert error.constraint_name == constraint
    assert (
        await factory.fetchval("SELECT count(*) FROM votes WHERE programme_id = :p", p=world.programme_id)
        == 0
    )
