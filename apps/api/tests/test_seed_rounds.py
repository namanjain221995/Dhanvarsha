"""`stagevote seed-rounds`: blank events to fill in later, nothing else."""

from __future__ import annotations

import pytest

from helpers import World
from stagevote.seed import ROUNDS, TEAM_MEMBERS, seed_rounds, seed_sample_content


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


async def test_sample_content_fills_only_what_is_empty(world: World) -> None:
    await seed_rounds(3)
    first = await world.factory.fetchrow(
        "SELECT id FROM events WHERE programme_id = :p AND position = 1", p=world.programme_id
    )
    await world.factory.execute("UPDATE events SET description = 'Our own words' WHERE id = :e", e=first.id)

    await seed_sample_content()
    rows = await world.factory.fetch(
        "SELECT e.position, e.description, n.name, n.members FROM events e "
        "JOIN ballots b ON b.event_id = e.id JOIN nominees n ON n.ballot_id = b.id "
        "WHERE e.programme_id = :p ORDER BY e.position, n.position",
        p=world.programme_id,
    )
    by_event: dict[int, list] = {}
    for r in rows:
        by_event.setdefault(r.position, []).append(r)
    assert sorted(by_event) == [1, 2, 3]
    assert all(4 <= len(teams) <= 6 for teams in by_event.values())
    assert all(r.members == TEAM_MEMBERS[r.name] and r.members for r in rows)
    assert by_event[1][0].description == "Our own words"  # an organiser's text is kept
    assert by_event[2][0].description == ROUNDS[1][1]

    count = await world.factory.fetchval(
        "SELECT count(*) FROM nominees n JOIN ballots b ON b.id = n.ballot_id JOIN events e ON e.id = b.event_id "
        "WHERE e.programme_id = :p",
        p=world.programme_id,
    )
    await seed_sample_content()  # nothing left to fill
    assert (
        await world.factory.fetchval(
            "SELECT count(*) FROM nominees n JOIN ballots b ON b.id = n.ballot_id JOIN events e ON e.id = b.event_id "
            "WHERE e.programme_id = :p",
            p=world.programme_id,
        )
        == count
    )
