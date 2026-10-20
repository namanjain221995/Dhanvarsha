"""Restarting the programme from round 1: old rounds are archived with their votes intact,
identical fresh copies take their place, and everyone can vote again."""

from __future__ import annotations

from helpers import World, assert_error


async def test_restart_archives_every_round_and_starts_again_from_round_one(world: World) -> None:
    admin = await world.admin()
    first = await world.event(nominees=2)
    second = await world.event(nominees=2)
    team = first.ballot.nominee_ids[0]
    await world.factory.execute(
        'UPDATE nominees SET members = \'["Asha Rao", "Ravi Kumar"]\'::jsonb WHERE id = :n', n=team
    )
    assert (await admin.open_event(first.id)).status_code == 200
    person = await world.participant()
    assert (await person.vote(first.ballot.id, team)).status_code == 201
    votes_before = await world.factory.fetchval(
        "SELECT count(*) FROM votes v JOIN events e ON e.id = v.event_id WHERE e.programme_id = :p",
        p=world.programme_id,
    )

    resp = await admin.post("/live/restart-programme", {"expected_live_version": await admin.live_version()})
    assert resp.status_code == 200, resp.text
    assert (resp.json()["events"], resp.json()["closed"]) == (2, 1)

    old = await world.factory.fetch(
        "SELECT id, status, archived_at FROM events WHERE id = ANY(:ids) ORDER BY position",
        ids=[first.id, second.id],
    )
    assert [(r.status, r.archived_at is not None) for r in old] == [("closed", True), ("upcoming", True)]
    fresh = await world.factory.fetch(
        "SELECT id, name, status, position FROM events WHERE programme_id = :p AND archived_at IS NULL "
        "ORDER BY position",
        p=world.programme_id,
    )
    assert [r.name for r in fresh] == [first.name, second.name]
    assert all(r.status == "upcoming" for r in fresh) and {r.id for r in fresh}.isdisjoint(
        {first.id, second.id}
    )
    copied = await world.factory.fetch(
        "SELECT n.name, n.members FROM nominees n JOIN ballots b ON b.id = n.ballot_id "
        "WHERE b.event_id = :e ORDER BY n.position",
        e=fresh[0].id,
    )
    original = await world.factory.fetch(
        "SELECT name, members FROM nominees WHERE ballot_id = :b ORDER BY position", b=first.ballot.id
    )
    assert [(r.name, r.members) for r in copied] == [(r.name, r.members) for r in original]
    assert copied[0].members == ["Asha Rao", "Ravi Kumar"]

    # Votes are never deleted: the old vote is still stored, only hidden with its round.
    assert (
        await world.factory.fetchval(
            "SELECT count(*) FROM votes v JOIN events e ON e.id = v.event_id WHERE e.programme_id = :p",
            p=world.programme_id,
        )
        == votes_before
    )
    snapshot = (await person.snapshot()).json()
    assert snapshot["receipts"] == [] and snapshot["live_event_id"] is None
    assert [e["id"] for e in snapshot["events"]] == [str(r.id) for r in fresh]

    # Round 1 again: the same participant votes again on the fresh copy; the archived ballot refuses.
    assert (await admin.open_event(fresh[0].id)).status_code == 200
    detail = (await person.event(fresh[0].id)).json()
    ballot = detail["ballots"][0]
    assert ballot["receipt"] is None
    assert ballot["nominees"][0]["members"] == ["Asha Rao", "Ravi Kumar"]
    again = await person.vote(ballot["id"], ballot["nominees"][1]["id"])
    assert again.status_code == 201, again.text
    late = await world.participant()
    assert (await late.vote(first.ballot.id, team)).status_code == 409

    audit = await world.factory.fetchrow(
        "SELECT details FROM audit_log WHERE action = 'programme.restarted' AND programme_id = :p",
        p=world.programme_id,
    )
    assert audit.details["events"] == 2


async def test_restart_needs_the_current_state_and_a_started_programme(world: World) -> None:
    admin = await world.admin()
    await world.event(nominees=1)
    version = await admin.live_version()
    assert_error(
        await admin.post("/live/restart-programme", {"expected_live_version": version}),
        409,
        "ALREADY_AT_START",
    )
    event = await world.event(nominees=1)
    assert (await admin.open_event(event.id)).status_code == 200
    assert_error(
        await admin.post("/live/restart-programme", {"expected_live_version": version}), 409, "STALE_STATE"
    )
    resp = await admin.post("/live/restart-programme", {"expected_live_version": await admin.live_version()})
    assert resp.status_code == 200, resp.text
