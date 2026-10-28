"""Manipulation attempts by a signed-in participant (or a hostile page) are refused."""

from __future__ import annotations

import uuid

from helpers import (
    ADMIN_API,
    ADMIN_COOKIE,
    API,
    DEVICE_COOKIE,
    PARTICIPANT_COOKIE,
    ParticipantClient,
    World,
    assert_error,
    dumps,
)
from stagevote.security import csrf_token_for


async def vote_count(world: World, participant_id: uuid.UUID) -> int:
    return int(
        await world.factory.fetchval("SELECT count(*) FROM votes WHERE participant_id = :p", p=participant_id)
    )


async def test_nominee_from_another_ballot_is_rejected(world: World) -> None:
    event = await world.live_event(ballots=2)
    voter = await world.participant()
    first, second = event.ballots
    assert_error(await voter.vote(first.id, second.nominee_ids[0]), 422, "INVALID_NOMINEE")
    assert_error(await voter.vote(first.id, uuid.uuid4()), 422, "INVALID_NOMINEE")
    assert await vote_count(world, voter.info.id) == 0
    # The rejected attempts did not burn the ballot: a valid choice is still accepted.
    assert (await voter.vote(first.id, first.nominee_ids[0])).status_code == 201


async def test_ballot_of_another_programme_is_not_found(world: World) -> None:
    voter = await world.participant()
    await world.live_event()
    # A second, inactive programme with an open event.
    other = await world.factory.create_programme(activate=False)
    foreign = await world.factory.create_event(other)
    await world.factory.execute(
        "UPDATE events SET status = 'open', opened_at = clock_timestamp(), "
        "voting_deadline_at = clock_timestamp() + interval '1 hour' WHERE id = :e",
        e=foreign.id,
    )
    assert_error(await voter.vote(foreign.ballot.id, foreign.ballot.nominee_ids[0]), 404, "NOT_FOUND")
    assert_error(await voter.event(foreign.id), 404, "NOT_FOUND")
    assert_error(await voter.receipt(foreign.ballot.id), 404, "NO_VOTE")
    assert await vote_count(world, voter.info.id) == 0


async def test_upcoming_event_cannot_be_voted_on_or_inspected(world: World) -> None:
    upcoming = await world.event()
    voter = await world.participant()
    assert_error(await voter.vote(upcoming.ballot.id, upcoming.ballot.nominee_ids[0]), 409, "VOTING_NOT_OPEN")
    assert_error(await voter.event(upcoming.id), 403, "EVENT_NOT_RELEASED")
    snapshot = (await voter.snapshot()).json()
    [shown] = snapshot["events"]
    assert shown["locked"] is True and shown["name"] is None and shown["ballots"] == []
    assert await vote_count(world, voter.info.id) == 0


async def test_participant_session_grants_no_admin_access(world: World) -> None:
    event = await world.live_event()
    voter = await world.participant()
    paths = ["/me", "/participants", "/live", "/results", "/email/jobs", f"/events/{event.id}"]
    for path in paths:
        assert_error(await voter.http.get(ADMIN_API + path), 401, "UNAUTHENTICATED")
    resp = await voter.http.post(
        f"{ADMIN_API}/live/close",
        json={"event_id": str(event.id), "expected_live_version": 1},
        headers={"X-CSRF-Token": voter.csrf or ""},
    )
    assert_error(resp, 401, "UNAUTHENTICATED")
    # Presenting the participant token as an admin cookie does not help either.
    forged = world.http()
    forged.cookies.set("sv_admin", voter.token or "")
    assert_error(await forged.get(f"{ADMIN_API}/me"), 401, "UNAUTHENTICATED")
    assert await world.factory.fetchval("SELECT status FROM events WHERE id = :e", e=event.id) == "open"


async def test_participants_cannot_see_or_use_each_others_data(world: World) -> None:
    event = await world.live_event()
    alice = await world.participant()
    bob = await world.participant()
    ballot = event.ballot
    bob_vote = await bob.vote(ballot.id, ballot.nominee_ids[2])
    assert bob_vote.status_code == 201

    assert_error(await alice.receipt(ballot.id), 404, "NO_VOTE")
    snapshot = (await alice.snapshot()).json()
    assert snapshot["receipts"] == []
    assert bob_vote.json()["receipt"]["receipt_code"] not in dumps(snapshot)
    detail = (await alice.event(event.id)).json()
    assert [b["receipt"] for b in detail["ballots"]] == [None]
    # Bob's CSRF token is bound to Bob's session and is useless with Alice's cookie.
    assert_error(await alice.vote(ballot.id, ballot.nominee_ids[0], csrf=bob.csrf), 403, "CSRF_FAILED")
    assert await vote_count(world, alice.info.id) == 0


async def test_cross_site_requests_are_refused(world: World) -> None:
    event = await world.live_event()
    voter = await world.participant()
    ballot = event.ballot

    def browser(origin: str | None, referer: str | None = None) -> ParticipantClient:
        client = world.browser(voter.info, origin=origin)
        client.http.cookies.set("sv_session", voter.token or "")
        if referer:
            client.http.headers["Referer"] = referer
        client.csrf = voter.csrf
        return client

    for origin in (None, "https://evil.example", "http://testserver.evil.example", "null"):
        attacker = browser(origin)
        assert_error(await attacker.vote(ballot.id, ballot.nominee_ids[0]), 403, "BAD_ORIGIN")
        assert_error(await attacker.logout(), 403, "BAD_ORIGIN")
        assert_error(await attacker.enter(), 403, "BAD_ORIGIN")
        assert_error(await attacker.check(), 403, "BAD_ORIGIN")
    admin = await world.admin()
    resp = await admin.http.post(
        f"{ADMIN_API}/live/close",
        json={"event_id": str(event.id), "expected_live_version": await admin.live_version()},
        headers={"X-CSRF-Token": admin.csrf, "Origin": "https://evil.example"},
    )
    assert_error(resp, 403, "BAD_ORIGIN")
    assert await vote_count(world, voter.info.id) == 0
    assert (await voter.snapshot()).status_code == 200  # the session survived the attempts

    # Without an Origin header, a same-site Referer is accepted (older browsers).
    same_site = browser(None, referer="http://testserver/rounds/1")
    assert (await same_site.vote(ballot.id, ballot.nominee_ids[0])).status_code == 201


async def test_a_cross_site_tap_is_refused_and_binds_nothing(world: World) -> None:
    person = await world.participant(login=False)
    hostile = world.browser(person.info, origin="https://evil.example")
    assert_error(await hostile.enter(), 403, "BAD_ORIGIN")
    assert hostile.token is None and hostile.device is None
    assert (
        await world.factory.fetchval(
            "SELECT link_device_hash FROM participants WHERE id = :p", p=person.info.id
        )
        is None
    )
    assert (await person.enter()).status_code == 200  # the real browser is still the first


async def test_the_device_cookie_alone_signs_nobody_in(world: World) -> None:
    voter = await world.participant()
    device = voter.device
    assert device is not None
    for name in (DEVICE_COOKIE, PARTICIPANT_COOKIE, ADMIN_COOKIE):
        forged = world.http()
        forged.cookies.set(name, device)
        assert_error(await forged.get(f"{API}/me/snapshot"), 401, "UNAUTHENTICATED")
        assert_error(await forged.get(f"{ADMIN_API}/me"), 401, "UNAUTHENTICATED")
        assert (await forged.get(f"{API}/auth/session")).json()["signed_in"] is False
    # With the device cookie but without the link, there is nothing to enter with.
    keeper = world.http()
    keeper.cookies.set(DEVICE_COOKIE, device)
    resp = await keeper.post(f"{API}/auth/link", json={"token": device})
    assert_error(resp, 401, "LINK_INVALID")


async def test_missing_or_wrong_csrf_token_is_refused(world: World) -> None:
    event = await world.live_event()
    voter = await world.participant()
    ballot = event.ballot
    session_id = await world.factory.fetchval(
        "SELECT id FROM participant_sessions WHERE participant_id = :p AND revoked_at IS NULL",
        p=voter.info.id,
    )
    assert_error(await voter.vote(ballot.id, ballot.nominee_ids[0], send_csrf=False), 403, "CSRF_FAILED")
    assert_error(await voter.vote(ballot.id, ballot.nominee_ids[0], csrf="0" * 64), 403, "CSRF_FAILED")
    admin_scoped = csrf_token_for(session_id, "admin")  # right session, wrong scope
    assert_error(await voter.vote(ballot.id, ballot.nominee_ids[0], csrf=admin_scoped), 403, "CSRF_FAILED")
    assert await vote_count(world, voter.info.id) == 0

    admin = await world.admin()
    resp = await admin.http.post(
        f"{ADMIN_API}/live/close", json={"event_id": str(event.id), "expected_live_version": 1}
    )
    assert_error(resp, 403, "CSRF_FAILED")
    # Safe methods need no token.
    assert (await voter.http.get(f"{API}/me/snapshot")).status_code == 200


async def test_results_are_visible_only_once_published(world: World) -> None:
    event = await world.live_event()
    alice = await world.participant()
    bob = await world.participant()
    ballot = event.ballot
    for voter, nominee in ((alice, ballot.nominee_ids[0]), (bob, ballot.nominee_ids[0])):
        assert (await voter.vote(ballot.id, nominee)).status_code == 201
    admin = await world.admin()

    assert_error(await admin.post(f"/events/{event.id}/results/publish"), 409, "EVENT_NOT_CLOSED")
    assert (await admin.close_event(event.id)).status_code == 200

    async def visible() -> tuple[bool, object]:
        snapshot = (await alice.snapshot()).json()
        [shown] = [e for e in snapshot["events"] if e["id"] == str(event.id)]
        detail = (await alice.event(event.id)).json()
        assert "total_votes" not in dumps(snapshot)
        return shown["results_published"], detail["results"]

    assert await visible() == (False, None)

    assert (await admin.post(f"/events/{event.id}/results/publish")).status_code == 200
    published, results = await visible()
    assert published is True
    assert isinstance(results, list)
    [tally] = results
    assert tally["total_votes"] == 2
    assert {n["nominee_id"]: n["votes"] for n in tally["nominees"]}[str(ballot.nominee_ids[0])] == 2

    assert (await admin.post(f"/events/{event.id}/results/unpublish")).status_code == 200
    assert await visible() == (False, None)


async def test_preview_setting_reveals_names_but_never_ballots_early(world: World) -> None:
    await world.factory.execute(
        "UPDATE programmes SET preview_upcoming = true, state_version = state_version + 1 WHERE id = :p",
        p=world.programme_id,
    )
    upcoming = await world.event(name="Secret Finale")
    voter = await world.participant()
    detail = (await voter.event(upcoming.id)).json()
    assert detail["event"]["name"] == "Secret Finale"
    assert detail["ballots"] == [] and detail["results"] is None
    assert str(upcoming.ballot.nominee_ids[0]) not in dumps(detail)
    assert_error(await voter.vote(upcoming.ballot.id, upcoming.ballot.nominee_ids[0]), 409, "VOTING_NOT_OPEN")
