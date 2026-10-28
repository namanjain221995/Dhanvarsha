"""Invitations create the sign-in: the email carries a personal link, and the first browser
that enters with it keeps it. No password, activation step, sign-up or self-service reset."""

from __future__ import annotations

import json
import re
import uuid
from datetime import timedelta
from typing import Any

import httpx

from helpers import API, Factory, World, assert_error, secret_of
from stagevote.security import new_token, token_hash

TOKEN = re.compile(r"[A-Za-z0-9_-]{43}")


async def invite(world: World, participant_id: uuid.UUID) -> httpx.Response:
    resp = await (await world.admin()).post(f"/participants/{participant_id}/invite")
    assert resp.status_code == 200, resp.text
    return resp


async def invitation_jobs(factory: Factory, participant_id: uuid.UUID) -> list[Any]:
    return await factory.fetch(
        "SELECT * FROM email_jobs WHERE participant_id = :p AND kind = 'invitation' ORDER BY created_at, id",
        p=participant_id,
    )


async def emailed_link_token(factory: Factory, participant_id: uuid.UUID) -> str:
    return secret_of((await invitation_jobs(factory, participant_id))[-1].secret_ciphertext)


async def participant_row(factory: Factory, participant_id: uuid.UUID) -> Any:
    return await factory.fetchrow("SELECT * FROM participants WHERE id = :p", p=participant_id)


async def session_count(factory: Factory, participant_id: uuid.UUID) -> int:
    return int(
        await factory.fetchval(
            "SELECT count(*) FROM participant_sessions WHERE participant_id = :p", p=participant_id
        )
    )


async def test_invitation_issues_a_link_and_keeps_only_an_encrypted_copy(world: World) -> None:
    person = await world.participant(login=False, activated=False)
    await invite(world, person.info.id)

    [job] = await invitation_jobs(world.factory, person.info.id)
    assert (job.status, job.recipient, job.attempts) == ("queued", person.info.email, 0)
    token = secret_of(job.secret_ciphertext)
    assert TOKEN.fullmatch(token)

    # The plaintext exists only as Fernet ciphertext in the job and as a SHA-256 on the
    # account: never in plain form in any row, the audit log or the outbox.
    assert token not in await world.factory.row_json("email_jobs", job.id)
    assert token.encode() not in bytes(job.secret_ciphertext)
    assert token not in await world.factory.row_json("participants", person.info.id)
    assert token not in await world.factory.programme_text(world.programme_id)
    row = await participant_row(world.factory, person.info.id)
    assert row.link_token_hash == token_hash(token) and row.link_issued_at is not None
    assert (row.link_device_hash, row.link_bound_at) == (None, None)  # no browser yet
    assert row.password_hash is None
    assert row.current_invitation_id == job.invitation_id
    assert row.activated_at is None  # nobody has signed in yet
    assert row.last_invited_at is not None


async def test_the_emailed_link_signs_in_and_binds_this_browser(world: World) -> None:
    person = await world.participant(login=False, activated=False)
    await invite(world, person.info.id)
    token = await emailed_link_token(world.factory, person.info.id)

    resp = await person.enter(token)
    assert resp.status_code == 200 and resp.json() == {"ok": True, "reused_session": False}
    device = person.device
    assert person.token is not None and device is not None
    assert await session_count(world.factory, person.info.id) == 1
    row = await participant_row(world.factory, person.info.id)
    assert row.activated_at is not None  # recorded as the first sign-in
    assert row.link_device_hash == token_hash(device) and row.link_bound_at is not None
    audit = await world.factory.fetch(
        "SELECT action, target_type, details->>'via' AS via, details::text AS details FROM audit_log "
        "WHERE actor_id = :p ORDER BY id",
        p=person.info.id,
    )
    assert [(a.action, a.target_type) for a in audit] == [
        ("participant.link_bound", "participant"),
        ("participant.login", "session"),
    ]
    assert audit[1].via == "link"
    assert not any(token in a.details or device in a.details for a in audit)
    snapshot = await person.snapshot()
    assert snapshot.status_code == 200, snapshot.text

    # Opening the email again in the same browser keeps the session.
    again = await person.enter(token)
    assert again.status_code == 200 and again.json() == {"ok": True, "reused_session": True}
    assert await session_count(world.factory, person.info.id) == 1


async def test_unknown_malformed_replaced_and_foreign_links_answer_identically(world: World) -> None:
    """The answer never tells which links exist or whose they were."""
    person = await world.participant(login=False, activated=False)
    await invite(world, person.info.id)
    replaced = await emailed_link_token(world.factory, person.info.id)
    await invite(world, person.info.id)  # the first link is replaced by the second
    other_programme = await world.factory.create_programme(activate=False)
    foreign = await world.factory.create_participant(other_programme)
    browser = world.browser(person.info)

    async def attempt(token: str) -> dict[str, Any]:
        assert (await browser.check(token)).json() == {"state": "invalid"}
        body = assert_error(await browser.enter(token), 401, "LINK_INVALID")
        return {k: body[k] for k in ("code", "message", "details")}

    unknown = await attempt(new_token())
    assert foreign.link_token is not None
    for token in (replaced, foreign.link_token, "example", "not a token", ""):
        assert await attempt(token) == unknown, token
    assert unknown["message"].startswith("This sign-in link is not valid any more.")
    assert await session_count(world.factory, person.info.id) == 0
    assert await session_count(world.factory, foreign.id) == 0


async def test_resending_replaces_the_link_releases_the_browser_and_signs_out_live(world: World) -> None:
    person = await world.participant(login=False, activated=False)
    await invite(world, person.info.id)
    first_token = await emailed_link_token(world.factory, person.info.id)
    assert (await person.enter(first_token)).status_code == 200
    session_id = await world.factory.fetchval(
        "SELECT id FROM participant_sessions WHERE participant_id = :p", p=person.info.id
    )

    resp = await invite(world, person.info.id)
    assert resp.json() == {"ok": True, "resent": True}
    second_token = await emailed_link_token(world.factory, person.info.id)
    assert first_token != second_token

    first, second = await invitation_jobs(world.factory, person.info.id)
    assert first.status == "superseded"
    assert first.secret_ciphertext is None and first.secret_purged_at is not None
    assert second.status == "queued" and second.secret_ciphertext is not None
    row = await participant_row(world.factory, person.info.id)
    assert row.current_invitation_id == second.invitation_id != first.invitation_id
    assert row.link_token_hash == token_hash(second_token)
    assert (row.link_device_hash, row.link_bound_at) == (None, None)

    # Signed out live: the session signal names exactly the session that ended.
    signal = await world.factory.fetchval(
        "SELECT payload::text FROM outbox_events WHERE topic = 'session' AND payload->>'pid' = :p "
        "ORDER BY id DESC LIMIT 1",
        p=str(person.info.id),
    )
    assert json.loads(signal) == {
        "pid": str(person.info.id),
        "sids": [str(session_id)],
        "r": "credentials_reissued",
    }
    error = assert_error(await person.snapshot(), 401, "SESSION_REVOKED")
    assert error["details"]["reason"] == "credentials_reissued"

    # The old link is dead even in the browser it was bound to. The new link belongs to
    # whichever browser enters with it first.
    assert_error(await person.enter(first_token), 401, "LINK_INVALID")
    fresh = world.browser(person.info)
    assert (await fresh.enter(second_token)).status_code == 200
    assert_error(await person.enter(second_token), 403, "LINK_IN_USE")


async def test_disabled_accounts_cannot_be_invited(world: World) -> None:
    admin = await world.admin()
    person = await world.participant(login=False, activated=False)
    assert (await admin.post(f"/participants/{person.info.id}/disable")).status_code == 200
    assert_error(await admin.post(f"/participants/{person.info.id}/invite"), 409, "ACCOUNT_DISABLED")
    assert await invitation_jobs(world.factory, person.info.id) == []


async def test_tapping_before_sign_in_opens_binds_nothing(world: World) -> None:
    await world.set_window(timedelta(hours=1), timedelta(hours=7))
    early = await world.participant(login=False, activated=False)
    await invite(world, early.info.id)
    token = await emailed_link_token(world.factory, early.info.id)
    assert (await early.check(token)).json() == {"state": "ready"}
    error = assert_error(await early.enter(token), 403, "LOGIN_NOT_OPEN")
    assert error["details"]["opens_at"] is not None

    await world.set_window(timedelta(hours=-3), timedelta(hours=-1))
    assert_error(await early.enter(token), 403, "LOGIN_CLOSED")
    row = await participant_row(world.factory, early.info.id)
    assert (row.link_device_hash, row.link_bound_at, row.activated_at) == (None, None, None)
    assert await session_count(world.factory, early.info.id) == 0

    # The early taps did not use the link up: once sign-in opens, any browser may still
    # be the first to enter.
    await world.set_window(timedelta(hours=-1), timedelta(hours=1))
    other = world.browser(early.info)
    assert (await other.enter(token)).status_code == 200
    assert_error(await early.enter(token), 403, "LINK_IN_USE")


async def test_password_sign_in_and_self_service_endpoints_do_not_exist(world: World) -> None:
    """Participants have no password: no sign-in with one, no sign-up, activation or reset."""
    http = world.http()
    for path in ("/auth/login", "/auth/activate", "/auth/recovery/request", "/auth/recovery/complete"):
        resp = await http.post(f"{API}{path}", json={"user_id": "someone@example.com", "password": "x"})
        assert resp.status_code in (404, 405), (path, resp.status_code)
    assert (await http.get(f"{API}/auth/login")).status_code in (404, 405)
    admin = await world.admin()
    person = await world.participant(login=False)
    for action in ("reminder", "recovery"):
        resp = await admin.post(f"/participants/{person.info.id}/{action}")
        assert resp.status_code in (404, 405), (action, resp.status_code)
    # Organisers still sign in with their email address and password.
    wrong = await http.post(f"{API}/admin/auth/login", json={"email": admin.info.email, "password": "wrong"})
    assert_error(wrong, 401, "INVALID_CREDENTIALS")
    right = await http.post(
        f"{API}/admin/auth/login", json={"email": admin.info.email, "password": admin.info.password}
    )
    assert right.status_code == 200, right.text


async def test_send_to_adds_new_people_and_emails_everyone_a_working_link(world: World) -> None:
    admin = await world.admin()
    existing = await world.participant(login=False, activated=False)
    await invite(world, existing.info.id)
    old_token = await emailed_link_token(world.factory, existing.info.id)
    disabled = await world.participant(login=False, activated=False)
    await world.factory.execute(
        "UPDATE participants SET disabled_at = now() WHERE id = :p", p=disabled.info.id
    )
    tag = uuid.uuid4().hex[:8]
    asha, ravi = f"asha-{tag}@example.com", f"ravi-{tag}@example.com"

    resp = await admin.post(
        "/invitations/send-to",
        {"to": f"{asha}, {existing.info.email.upper()}\nRavi Kumar <{ravi}>\n{disabled.info.email}"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert (body["created"], body["invited"], body["resent"]) == (2, 3, 1)
    assert body["disabled"] == [disabled.info.email]
    assert body["recipients"] == sorted([asha, ravi, existing.info.email])

    for address, name in ((asha, ""), (ravi, "Ravi Kumar")):
        row = await world.factory.fetchrow(
            "SELECT id, display_name, programme_id FROM participants WHERE email = :e", e=address
        )
        assert (row.display_name, row.programme_id) == (name, world.programme_id)
        token = await emailed_link_token(world.factory, row.id)
        assert TOKEN.fullmatch(token)
        resp = await world.http().post(f"{API}/auth/link", json={"token": token})
        assert resp.status_code == 200, resp.text

    # Someone invited before gets a new link; the old one stops working.
    replacement = await emailed_link_token(world.factory, existing.info.id)
    assert replacement != old_token
    assert_error(await world.browser(existing.info).enter(old_token), 401, "LINK_INVALID")
    assert (await world.browser(existing.info).enter(replacement)).status_code == 200
    assert await invitation_jobs(world.factory, disabled.info.id) == []


async def test_send_to_is_all_or_nothing_and_bounded(world: World) -> None:
    admin = await world.admin()
    tag = uuid.uuid4().hex[:8]
    error = assert_error(
        await admin.post("/invitations/send-to", {"to": f"ok-{tag}@example.com, broken-{tag}@nowhere"}),
        422,
        "VALIDATION_FAILED",
    )
    assert f"broken-{tag}@nowhere" in error["message"] and "Nothing was sent" in error["message"]
    many = ", ".join(f"p{i}-{tag}@example.com" for i in range(51))
    assert_error(await admin.post("/invitations/send-to", {"to": many}), 422, "VALIDATION_FAILED")
    assert (
        await world.factory.fetchval("SELECT count(*) FROM participants WHERE email LIKE :t", t=f"%{tag}%")
    ) == 0
