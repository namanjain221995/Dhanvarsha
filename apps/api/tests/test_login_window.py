"""The login window and voting deadlines are decided by server (database) time only."""

from __future__ import annotations

import time
from datetime import datetime, timedelta
from email.utils import formatdate

from helpers import API, World, assert_error, wait_until_db_time


def _client_claims(offset_seconds: float) -> dict[str, str]:
    """Headers a manipulated client could send to claim a different time."""
    claimed = time.time() + offset_seconds
    return {
        "Date": formatdate(claimed, usegmt=True),
        "X-Client-Time": datetime.fromtimestamp(claimed).isoformat(),
        "If-Modified-Since": formatdate(claimed, usegmt=True),
    }


async def test_login_refused_before_open_and_after_close_whatever_the_client_claims(world: World) -> None:
    person = await world.participant(login=False)
    link = {"token": person.info.link_token}

    await world.set_window(timedelta(hours=1), timedelta(hours=5))
    opens_at = await world.factory.fetchval(
        "SELECT login_opens_at FROM programmes WHERE id = :p", p=world.programme_id
    )
    resp = await person.http.post(f"{API}/auth/link", json=link, headers=_client_claims(2 * 3600))
    error = assert_error(resp, 403, "LOGIN_NOT_OPEN")
    assert datetime.fromisoformat(error["details"]["opens_at"]) == opens_at
    public = (await person.http.get(f"{API}/public/programme", headers=_client_claims(2 * 3600))).json()
    assert public["login_phase"] == "before_open"
    server_time = datetime.fromisoformat(public["server_time"])
    assert abs((server_time - await world.factory.now()).total_seconds()) < 5

    await world.set_window(timedelta(hours=-5), timedelta(hours=-1))
    resp = await person.http.post(f"{API}/auth/link", json=link, headers=_client_claims(-2 * 3600))
    assert_error(resp, 403, "LOGIN_CLOSED")
    assert (await person.http.get(f"{API}/public/programme")).json()["login_phase"] == "closed"

    assert (
        await world.factory.fetchval(
            "SELECT count(*) FROM participant_sessions WHERE participant_id = :p", p=person.info.id
        )
        == 0
    )
    # A refused tap binds nothing: once the window opens, another browser may still enter.
    assert (
        await world.factory.fetchval(
            "SELECT link_device_hash FROM participants WHERE id = :p", p=person.info.id
        )
        is None
    )
    await world.set_window(timedelta(hours=-1), timedelta(hours=1))
    assert (await world.browser(person.info).enter()).status_code == 200


async def test_login_window_boundary_follows_database_time(world: World) -> None:
    person = await world.participant(login=False)
    await world.set_window(timedelta(seconds=1.5), timedelta(hours=2))
    opens_at = await world.factory.fetchval(
        "SELECT login_opens_at FROM programmes WHERE id = :p", p=world.programme_id
    )

    assert_error(await person.enter(), 403, "LOGIN_NOT_OPEN")
    await wait_until_db_time(world.factory, opens_at)
    resp = await person.enter()
    assert resp.status_code == 200, resp.text


async def test_deadline_is_enforced_with_database_time_before_any_sweep(world: World) -> None:
    event = await world.live_event(ballots=2)
    voter = await world.participant()
    [(deadline,)] = await world.factory.execute(
        "UPDATE events SET voting_deadline_at = clock_timestamp() + interval '2 seconds' WHERE id = :e "
        "RETURNING voting_deadline_at",
        e=event.id,
    )
    await world.factory.touch(world.programme_id)
    first, second = event.ballots

    resp = await voter.vote(first.id, first.nominee_ids[0])
    assert resp.status_code == 201, resp.text
    await wait_until_db_time(world.factory, deadline)

    # Nothing has closed the event yet; the stored status is still "open".
    assert await world.factory.fetchval("SELECT status FROM events WHERE id = :e", e=event.id) == "open"
    assert_error(await voter.vote(second.id, second.nominee_ids[0]), 409, "VOTING_CLOSED")
    snapshot = (await voter.snapshot()).json()
    [shown] = [e for e in snapshot["events"] if e["id"] == str(event.id)]
    assert shown["status"] == "closed"
    assert snapshot["live_event_id"] is None

    votes = await world.factory.fetch(
        "SELECT ballot_id, created_at FROM votes WHERE participant_id = :p", p=voter.info.id
    )
    assert [v.ballot_id for v in votes] == [first.id]
    assert votes[0].created_at < deadline


async def test_signed_in_participant_cannot_vote_once_access_closes(world: World) -> None:
    event = await world.live_event()
    voter = await world.participant()
    await world.factory.execute(
        "UPDATE programmes SET login_closes_at = clock_timestamp() - interval '1 second' WHERE id = :p",
        p=world.programme_id,
    )
    assert_error(
        await voter.vote(event.ballot.id, event.ballot.nominee_ids[0]), 409, "PROGRAMME_ACCESS_CLOSED"
    )
    assert (await voter.snapshot()).json()["phase"] == "access_closed"
    assert (
        await world.factory.fetchval("SELECT count(*) FROM votes WHERE participant_id = :p", p=voter.info.id)
        == 0
    )
