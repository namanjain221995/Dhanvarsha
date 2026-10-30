"""Admin API details found while building the console: round numbers, participant counts,
nominee order freezing, email job scoping, organiser session status and logout signals."""

from __future__ import annotations

import uuid

from helpers import ADMIN_API, API, World, assert_error


async def test_rounds_count_only_non_archived_events(world: World) -> None:
    admin = await world.admin()
    first = await world.event(name="First")
    second = await world.event(name="Second")
    third = await world.event(name="Third")
    resp = await admin.post(f"/events/{first.id}/archive")
    assert resp.status_code == 200, resp.text

    detail = (await admin.get(f"/events/{third.id}")).json()["event"]
    assert detail["round"] == 2  # the archived first event does not count
    archived = (await admin.get(f"/events/{first.id}")).json()["event"]
    assert archived["archived"] is True and archived["round"] is None

    created = await admin.post("/events", {"name": "Fourth"})
    assert created.status_code == 201, created.text
    assert created.json()["event"]["round"] == 3

    patched = await admin.patch(f"/events/{second.id}", {"version": 1, "description": "Updated"})
    assert patched.status_code == 200, patched.text
    assert patched.json()["event"]["round"] == 1


async def test_nominee_order_freezes_when_voting_opens(world: World) -> None:
    admin = await world.admin()
    event = await world.event(nominees=3)
    ballot = event.ballot
    reversed_ids = [str(n) for n in reversed(ballot.nominee_ids)]
    resp = await admin.post(f"/ballots/{ballot.id}/nominees/reorder", {"ordered_ids": reversed_ids})
    assert resp.status_code == 200, resp.text

    assert (await admin.open_event(event.id)).status_code == 200
    resp = await admin.post(
        f"/ballots/{ballot.id}/nominees/reorder", {"ordered_ids": [str(n) for n in ballot.nominee_ids]}
    )
    assert_error(resp, 409, "BALLOT_FROZEN")


async def test_participant_counts_and_completion_ignore_archived_rounds(world: World) -> None:
    admin = await world.admin()
    voter = await world.participant()
    await world.participant(login=False, activated=False)  # imported, never invited
    live = await world.live_event()
    resp = await voter.vote(live.ballot.id, live.ballot.nominee_ids[0])
    assert resp.status_code == 201, resp.text
    assert (await admin.close_event(live.id)).status_code == 200
    assert (await admin.post(f"/events/{live.id}/archive")).status_code == 200

    data = (await admin.get("/participants")).json()
    counts = data["counts"]
    assert {"all", "not_invited", "invited", "activated", "disabled", "signed_in", "invite_failed"} <= set(
        counts
    )
    assert counts["signed_in"] == 1 and counts["not_invited"] == 1 and counts["invited"] == 0
    row = next(p for p in data["items"] if p["id"] == str(voter.info.id))
    # The only vote is in an archived round, which no longer counts as an opened ballot.
    assert data["opened_ballots"] == 0
    assert row["ballots_voted"] == 0


async def test_email_jobs_are_scoped_to_the_active_programme(world: World) -> None:
    admin = await world.admin()
    other = await world.factory.create_programme(activate=False)
    job_id = uuid.uuid4()
    await world.factory.execute(
        "INSERT INTO email_jobs (id, programme_id, kind, recipient, status, attempts, max_attempts, message_id) "
        "VALUES (:id, :p, 'test', 'someone@example.com', 'failed', 1, 1, :m)",
        id=job_id,
        p=other,
        m=f"<{job_id}@example.com>",
    )
    assert_error(await admin.get(f"/email/jobs/{job_id}"), 404, "NOT_FOUND")
    assert_error(await admin.post(f"/email/jobs/{job_id}/retry", {}), 404, "NOT_FOUND")


async def test_organiser_session_status_and_logout_signal(world: World) -> None:
    admin = await world.admin()
    status = await admin.http.get(f"{API}/admin/auth/session")
    assert status.status_code == 200 and status.json() == {"signed_in": True}

    session_id = await world.factory.fetchval(
        "SELECT id FROM admin_sessions WHERE admin_id = :a AND revoked_at IS NULL", a=admin.info.id
    )
    resp = await admin.http.post(f"{API}/admin/auth/logout")
    assert resp.status_code == 200, resp.text
    status = await admin.http.get(f"{API}/admin/auth/session")
    assert status.json() == {"signed_in": False}
    assert_error(await admin.http.get(f"{ADMIN_API}/me"), 401, "UNAUTHENTICATED")

    # Every API process closes this session's admin sockets through the outbox signal.
    signal = await world.factory.fetchrow(
        "SELECT payload FROM outbox_events WHERE topic = 'admin_session' AND payload->>'sid' = :s",
        s=str(session_id),
    )
    assert signal is not None
    assert signal.payload["aid"] == str(admin.info.id)


async def test_hub_closes_only_the_signed_out_admin_session() -> None:
    from stagevote.live.hub import Connection, LiveHub

    hub = LiveHub("postgresql+asyncpg://unused@localhost/unused")
    admin_id, kept_session, ended_session = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    kept = Connection(
        ws=None, role="admin", programme_id=None, session_id=kept_session, principal_id=admin_id
    )  # type: ignore[arg-type]
    ended = Connection(
        ws=None, role="admin", programme_id=None, session_id=ended_session, principal_id=admin_id
    )  # type: ignore[arg-type]
    hub.register(kept)
    hub.register(ended)
    hub.dispatch(
        {"id": 1, "p": None, "t": "admin_session", "d": {"aid": str(admin_id), "sid": str(ended_session)}}
    )
    assert ended.close_code == 4401
    assert kept.close_code is None


async def test_the_lobby_welcome_is_set_in_the_programme_settings(world: World) -> None:
    """The lobby's text while no round is open: empty until an organiser writes one."""
    admin = await world.admin()
    voter = await world.participant()
    programme = (await voter.snapshot()).json()["programme"]
    assert (programme["welcome_title"], programme["welcome_message"]) == ("", "")

    version = await admin.settings_version()
    resp = await admin.patch(
        "/programme",
        {
            "settings_version": version,
            "welcome_title": "  Welcome to Dhanvarsha 2026 ",
            "welcome_message": "Keep this page open.\r\nThe first round opens soon.",
        },
    )
    assert resp.status_code == 200, resp.text
    assert (resp.json()["welcome_title"], resp.json()["welcome_message"]) == (
        "Welcome to Dhanvarsha 2026",
        "Keep this page open.\nThe first round opens soon.",
    )
    programme = (await voter.snapshot()).json()["programme"]
    assert (programme["welcome_title"], programme["welcome_message"]) == (
        "Welcome to Dhanvarsha 2026",
        "Keep this page open.\nThe first round opens soon.",
    )
    audit = await world.factory.fetchval(
        "SELECT details->'welcome_title'->>'to' FROM audit_log WHERE action = 'programme.updated' "
        "AND programme_id = :p ORDER BY created_at DESC LIMIT 1",
        p=world.programme_id,
    )
    assert audit == "Welcome to Dhanvarsha 2026"

    # One line for the heading; limits on both.
    version = await admin.settings_version()
    for field, value in (
        ("welcome_title", "Welcome\nBcc: x@example.com"),
        ("welcome_title", "w" * 121),
        ("welcome_message", "m" * 1001),
    ):
        resp = await admin.patch("/programme", {"settings_version": version, field: value})
        assert_error(resp, 422, "VALIDATION_FAILED")

    # Empty brings back the built-in text.
    resp = await admin.patch(
        "/programme", {"settings_version": version, "welcome_title": "", "welcome_message": ""}
    )
    assert resp.status_code == 200, resp.text
    programme = (await voter.snapshot()).json()["programme"]
    assert (programme["welcome_title"], programme["welcome_message"]) == ("", "")


async def test_names_reject_control_characters(world: World) -> None:
    """Names reach email headers; a newline must never get that far."""
    admin = await world.admin()
    version = await admin.settings_version()
    resp = await admin.patch("/programme", {"settings_version": version, "name": "Night\nBcc: x@example.com"})
    assert_error(resp, 422, "VALIDATION_FAILED")
    resp = await admin.post("/events", {"name": "Round\r\nOne"})
    assert_error(resp, 422, "VALIDATION_FAILED")
    resp = await admin.post("/events", {"name": "Round “One” — finals"})
    assert resp.status_code == 201, resp.text
