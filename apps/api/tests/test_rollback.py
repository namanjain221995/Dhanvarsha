"""A failure inside a transaction leaves no trace: no vote, no receipt, no notification."""

from __future__ import annotations

from typing import Any

import pytest
from sqlalchemy import text

from helpers import World, assert_error
from stagevote.services import live as live_service
from stagevote.services import voting


async def test_failure_after_the_vote_insert_rolls_the_vote_back(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    event = await world.live_event()
    voter = await world.participant()
    ballot = event.ballot
    outbox_before = await world.factory.fetchval(
        "SELECT count(*) FROM outbox_events WHERE programme_id = :p", p=world.programme_id
    )
    seen_inside: list[int] = []

    async def failing_emit(db: Any, *args: Any, **kwargs: Any) -> None:
        # The vote row exists inside the transaction at this point...
        seen_inside.append(
            (
                await db.execute(
                    text("SELECT count(*) FROM votes WHERE participant_id = :p"), {"p": voter.info.id}
                )
            ).scalar_one()
        )
        raise RuntimeError("notification store unavailable")

    monkeypatch.setattr(voting, "emit", failing_emit)
    resp = await voter.vote(ballot.id, ballot.nominee_ids[0], key="doomed")
    error = assert_error(resp, 500, "INTERNAL_ERROR")
    assert error["details"] == {} and "receipt" not in resp.json()
    assert seen_inside == [1]

    # ...and nothing of it survived.
    assert (
        await world.factory.fetchval("SELECT count(*) FROM votes WHERE participant_id = :p", p=voter.info.id)
        == 0
    )
    assert (
        await world.factory.fetchval(
            "SELECT count(*) FROM outbox_events WHERE programme_id = :p", p=world.programme_id
        )
        == outbox_before
    )
    assert_error(await voter.receipt(ballot.id), 404, "NO_VOTE")

    # The retry with the same key is a first submission, not a replay of a ghost vote.
    monkeypatch.undo()
    retry = await voter.vote(ballot.id, ballot.nominee_ids[0], key="doomed")
    assert retry.status_code == 201 and retry.json()["replayed"] is False


async def test_failed_transition_leaves_the_live_state_untouched(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    event = await world.live_event()
    voter = await world.participant()
    admin = await world.admin()
    version = await admin.live_version()
    outbox_before = await world.factory.fetchval(
        "SELECT count(*) FROM outbox_events WHERE programme_id = :p", p=world.programme_id
    )

    async def failing_audit(*args: Any, **kwargs: Any) -> None:
        raise RuntimeError("audit store unavailable")

    monkeypatch.setattr(live_service, "audit", failing_audit)
    assert_error(await admin.close_event(event.id, expected=version), 500, "INTERNAL_ERROR")
    monkeypatch.undo()

    row = await world.factory.fetchrow("SELECT status, closed_at FROM events WHERE id = :e", e=event.id)
    assert (row.status, row.closed_at) == ("open", None)
    assert await admin.live_version() == version
    assert (
        await world.factory.fetchval(
            "SELECT count(*) FROM outbox_events WHERE programme_id = :p", p=world.programme_id
        )
        == outbox_before
    )
    assert (await voter.vote(event.ballot.id, event.ballot.nominee_ids[0])).status_code == 201
