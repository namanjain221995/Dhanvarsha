"""`stagevote seed-rounds`: the 14 sample rounds as an editable starting line-up, nothing else."""

from __future__ import annotations

import pytest

from helpers import World
from stagevote.seed import ROUNDS, TEAM_MEMBERS, seed_rounds


async def test_seed_rounds_adds_the_sample_line_up_and_nothing_else(world: World) -> None:
    before = await world.factory.fetchrow(
        "SELECT name, (SELECT count(*) FROM participants WHERE programme_id = :p) AS people "
        "FROM programmes WHERE id = :p",
        p=world.programme_id,
    )
    await seed_rounds()

    events = await world.factory.fetch(
        "SELECT id, name, status, position FROM events WHERE programme_id = :p AND archived_at IS NULL "
        "ORDER BY position",
        p=world.programme_id,
    )
    assert [e.name for e in events] == [name for name, _, _ in ROUNDS]
    assert all(e.status == "upcoming" for e in events) and [e.position for e in events] == list(range(1, 15))
    teams = await world.factory.fetch(
        "SELECT n.name, n.members FROM nominees n JOIN ballots b ON b.id = n.ballot_id "
        "JOIN events e ON e.id = b.event_id WHERE e.programme_id = :p AND b.kind = 'team'",
        p=world.programme_id,
    )
    assert teams and all(t.members == TEAM_MEMBERS[t.name] for t in teams)

    after = await world.factory.fetchrow(
        "SELECT name, (SELECT count(*) FROM participants WHERE programme_id = :p) AS people "
        "FROM programmes WHERE id = :p",
        p=world.programme_id,
    )
    assert (after.name, after.people) == (before.name, before.people)
    assert (
        await world.factory.fetchval(
            "SELECT count(*) FROM audit_log WHERE action = 'events.seeded' AND programme_id = :p",
            p=world.programme_id,
        )
        == 1
    )

    with pytest.raises(SystemExit, match="already has 14 rounds"):
        await seed_rounds()
