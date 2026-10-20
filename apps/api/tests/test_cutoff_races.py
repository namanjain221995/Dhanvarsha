"""Races at the voting cutoff: votes against close/start-next, deadlines and stale admins.

Votes take the programme live lock SHARED and transitions take it EXCLUSIVE, so every
vote is ordered strictly before or after a transition. Several tests hold the exclusive
lock from a separate transaction (`lock_live_exclusive`) to line requests up in the lock
queue deterministically, then release it.
"""

from __future__ import annotations

import asyncio
import random
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import timedelta
from typing import Any

import httpx
import pytest

from helpers import (
    AdminClient,
    EventInfo,
    ParticipantClient,
    World,
    advisory_lock_waiters,
    assert_error,
    eventually,
    wait_until_db_time,
)
from stagevote.db import sessionmaker
from stagevote.state import lock_live_exclusive


@asynccontextmanager
async def live_lock_held(world: World) -> AsyncIterator[None]:
    """Hold the programme live lock EXCLUSIVE in another transaction until the block exits."""
    async with sessionmaker()() as holder, holder.begin():
        await lock_live_exclusive(holder, world.programme_id)
        yield


async def queued(world: World, count: int) -> None:
    async def probe() -> bool:
        return await advisory_lock_waiters(world.factory) >= count

    await eventually(probe, within=10, what=f"{count} transactions queued on the live lock")


async def ballot_votes(world: World, ballot_id: uuid.UUID) -> dict[uuid.UUID, Any]:
    rows = await world.factory.fetch(
        "SELECT participant_id, created_at FROM votes WHERE ballot_id = :b", b=ballot_id
    )
    return {r.participant_id: r.created_at for r in rows}


async def test_votes_queued_before_a_close_count_and_votes_queued_after_it_do_not(world: World) -> None:
    event = await world.live_event()
    early = [await world.participant() for _ in range(3)]
    late = [await world.participant() for _ in range(3)]
    admin = await world.admin()
    version = await admin.live_version()
    ballot = event.ballot

    async with live_lock_held(world):
        early_votes = [asyncio.create_task(p.vote(ballot.id, ballot.nominee_ids[0])) for p in early]
        await queued(world, 3)
        close = asyncio.create_task(admin.close_event(event.id, expected=version))
        await queued(world, 4)
        late_votes = [asyncio.create_task(p.vote(ballot.id, ballot.nominee_ids[1])) for p in late]
        await queued(world, 7)
        assert not any(t.done() for t in [*early_votes, close, *late_votes])

    assert [r.status_code for r in await asyncio.gather(*early_votes)] == [201, 201, 201]
    assert (await close).status_code == 200
    for resp in await asyncio.gather(*late_votes):
        assert_error(resp, 409, "VOTING_CLOSED")

    closed_at = await world.factory.fetchval("SELECT closed_at FROM events WHERE id = :e", e=event.id)
    stored = await ballot_votes(world, ballot.id)
    assert set(stored) == {p.info.id for p in early}
    assert all(created <= closed_at for created in stored.values())


@pytest.mark.parametrize("transition", ["close", "start_next"])
async def test_votes_racing_a_transition_are_either_counted_before_it_or_refused(
    world: World, transition: str
) -> None:
    rng = random.Random(f"{transition}-race")
    voters = [await world.participant() for _ in range(8)]
    admin = await world.admin()
    current = await world.live_event(nominees=2)
    outcomes = {"accepted": 0, "refused": 0}

    for _ in range(8):
        following = await world.event(nominees=2)
        version = await admin.live_version()

        async def cast(voter: ParticipantClient, event: EventInfo = current) -> httpx.Response:
            await asyncio.sleep(rng.uniform(0, 0.04))
            return await voter.vote(event.ballot.id, event.ballot.nominee_ids[rng.randrange(2)])

        async def switch(
            event: EventInfo = current, nxt: EventInfo = following, v: int = version
        ) -> httpx.Response:
            await asyncio.sleep(0.015)
            if transition == "close":
                return await admin.close_event(event.id, expected=v)
            return await admin.start_next(nxt.id, expected=v)

        switched, *votes = await asyncio.gather(switch(), *(cast(v) for v in voters))
        assert switched.status_code == 200, switched.text

        row = await world.factory.fetchrow(
            "SELECT status, closed_at, close_reason FROM events WHERE id = :e", e=current.id
        )
        assert row.status == "closed"
        assert row.close_reason == ("manual" if transition == "close" else "superseded")
        stored = await ballot_votes(world, current.ballot.id)
        for voter, resp in zip(voters, votes, strict=True):
            if resp.status_code == 201:
                assert stored[voter.info.id] <= row.closed_at
                outcomes["accepted"] += 1
            else:
                assert_error(resp, 409, "VOTING_CLOSED")
                assert voter.info.id not in stored
                outcomes["refused"] += 1
        assert len(stored) == sum(1 for r in votes if r.status_code == 201)

        if transition == "close":
            assert (await admin.open_event(following.id)).status_code == 200
        current = following

    assert sum(outcomes.values()) == 8 * len(voters)
    # The timing straddles the transition: both outcomes must actually occur (about half
    # each in practice), otherwise this test would not be exercising the race at all.
    assert outcomes["accepted"] > 0 and outcomes["refused"] > 0, outcomes


@pytest.mark.parametrize("deadline_passes_while_waiting", [True, False])
async def test_vote_waiting_for_the_live_lock_is_judged_when_it_gets_the_lock(
    world: World, deadline_passes_while_waiting: bool
) -> None:
    """The acceptance check reads clock_timestamp() after the lock, not at request start."""
    event = await world.live_event()
    voter = await world.participant()
    window = "1.5 seconds" if deadline_passes_while_waiting else "10 minutes"
    [(deadline,)] = await world.factory.execute(
        f"UPDATE events SET voting_deadline_at = clock_timestamp() + interval '{window}' WHERE id = :e "
        "RETURNING voting_deadline_at",
        e=event.id,
    )
    await world.factory.touch(world.programme_id)

    async with live_lock_held(world):
        submitted_at = await world.factory.now()
        vote = asyncio.create_task(voter.vote(event.ballot.id, event.ballot.nominee_ids[0]))
        await queued(world, 1)
        assert submitted_at < deadline  # the request arrived in time...
        await wait_until_db_time(
            world.factory, min(deadline, submitted_at + timedelta(seconds=1.8)), margin=0.3
        )
        assert not vote.done()  # ...but is still waiting for the lock

    resp = await vote
    if deadline_passes_while_waiting:
        assert_error(resp, 409, "VOTING_CLOSED")
        assert await ballot_votes(world, event.ballot.id) == {}
    else:
        assert resp.status_code == 201, resp.text
        assert list(await ballot_votes(world, event.ballot.id)) == [voter.info.id]


async def _race_admins(world: World, actions: list[Any]) -> list[httpx.Response]:
    """Queue every admin request behind the live lock, then let them run in order."""
    async with live_lock_held(world):
        tasks = [asyncio.create_task(action) for action in actions]
        await queued(world, len(actions))
    return list(await asyncio.gather(*tasks))


def _one_winner(responses: list[httpx.Response], version: int) -> httpx.Response:
    winners = [r for r in responses if r.status_code == 200]
    assert len(winners) == 1, [(r.status_code, r.text) for r in responses]
    for resp in responses:
        if resp is not winners[0]:
            error = assert_error(resp, 409, "STALE_STATE")
            assert error["details"]["live_version"] == version + 1
    return winners[0]


async def test_concurrent_admin_actions_with_the_same_live_version(world: World) -> None:
    first = await world.live_event()
    second = await world.event()
    third = await world.event()
    alice: AdminClient = await world.admin()
    bob = await world.admin(fresh=True)

    version = await alice.live_version()
    responses = await _race_admins(
        world, [alice.start_next(second.id, expected=version), bob.start_next(third.id, expected=version)]
    )
    winner = _one_winner(responses, version)
    opened = second if winner is responses[0] else third
    rows = await world.factory.fetch(
        "SELECT id, status, close_reason FROM events WHERE programme_id = :p ORDER BY position",
        p=world.programme_id,
    )
    status = {r.id: (r.status, r.close_reason) for r in rows}
    assert status[first.id] == ("closed", "superseded")
    assert status[opened.id] == ("open", None)
    assert [r.id for r in rows if r.status == "open"] == [opened.id]

    version = await alice.live_version()
    deadline = (await world.factory.now()) + timedelta(minutes=30)
    responses = await _race_admins(
        world,
        [
            alice.close_event(opened.id, expected=version),
            bob.post(
                "/live/deadline",
                {
                    "event_id": str(opened.id),
                    "expected_live_version": version,
                    "deadline_at": deadline.isoformat(),
                },
            ),
        ],
    )
    winner = _one_winner(responses, version)
    state = await world.factory.fetchrow(
        "SELECT status, voting_deadline_at FROM events WHERE id = :e", e=opened.id
    )
    if winner is responses[0]:
        assert state.status == "closed"
    else:
        assert state.status == "open" and state.voting_deadline_at == deadline
