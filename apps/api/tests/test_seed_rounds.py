"""`stagevote seed-rounds`: blank events to fill in later, nothing else."""

from __future__ import annotations

import pytest

from helpers import World
from stagevote.seed import seed_rounds


async def test_seed_rounds_adds_blank_events_and_nothing_else(world: World) -> None:
    before = await world.factory.fetchrow(
        "SELECT name, (SELECT count(*) FROM participants WHERE programme_id = :p) AS people "
        "FROM programmes WHERE id = :p",
        p=world.programme_id,
    )
    await seed_rounds(14)

    events = await world.factory.fetch(
        "SELECT e.name, e.status, e.position, b.title, b.kind, "
        "(SELECT count(*) FROM nominees n WHERE n.ballot_id = b.id) AS nominees "
        "FROM events e JOIN ballots b ON b.event_id = e.id "
        "WHERE e.programme_id = :p AND e.archived_at IS NULL ORDER BY e.position",
        p=world.programme_id,
    )
    assert [e.name for e in events] == [f"Event {i}" for i in range(1, 15)]
    assert {(e.status, e.title, e.kind, e.nominees) for e in events} == {("upcoming", "Best team", "team", 0)}

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
    with pytest.raises(SystemExit, match="already has 14 events"):
        await seed_rounds(14)
