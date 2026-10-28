"""Participant sessions: one browser per sign-in link, and every way a session ends."""

from __future__ import annotations

import asyncio
import uuid
from datetime import timedelta
from email.utils import parsedate_to_datetime
from typing import Any

import httpx
import pytest

from helpers import (
    API,
    DEVICE_COOKIE,
    ParticipantClient,
    World,
    assert_error,
    secret_of,
)
from stagevote.db import sessionmaker
from stagevote.deps import REVOKE_MESSAGES, ParticipantContext
from stagevote.errors import AppError
from stagevote.security import new_token, token_hash
from stagevote.services import auth as auth_service
from stagevote.services import voting


async def sessions(world: World, participant_id: uuid.UUID) -> list[Any]:
    return await world.factory.fetch(
        "SELECT * FROM participant_sessions WHERE participant_id = :p ORDER BY created_at", p=participant_id
    )


async def binding(world: World, participant_id: uuid.UUID) -> Any:
    return await world.factory.fetchrow(
        "SELECT link_device_hash, link_bound_at, activated_at FROM participants WHERE id = :p",
        p=participant_id,
    )


async def audited(world: World, participant_id: uuid.UUID) -> list[str]:
    """Audit actions recorded with this participant as the actor, oldest first."""
    rows = await world.factory.fetch(
        "SELECT action FROM audit_log WHERE actor_id = :p ORDER BY id", p=participant_id
    )
    return [r.action for r in rows]


async def failures(world: World) -> int:
    """Failed sign-in attempts counted against this test's client address."""
    return int(
        await world.factory.fetchval(
            "SELECT coalesce(sum(count), 0) FROM rate_limit_buckets WHERE key = :k",
            k=f"login_fail:ip:{world.ip}",
        )
    )


async def live_context(world: World, voter: ParticipantClient) -> ParticipantContext:
    """What the auth dependency hands the vote route for this session right now."""
    row = await world.factory.fetchrow(
        "SELECT * FROM participant_sessions WHERE participant_id = :p AND revoked_at IS NULL", p=voter.info.id
    )
    return ParticipantContext(
        session_id=row.id,
        participant_id=voter.info.id,
        programme_id=world.programme_id,
        user_code=voter.info.user_code,
        display_name="Test Participant",
        email=voter.info.email,
        expires_at=row.expires_at,
        ip="10.9.9.9",
    )


async def test_check_says_what_the_page_should_offer_and_changes_nothing(world: World) -> None:
    person = await world.participant(login=False)

    # The first look at the link hands this browser a device cookie and binds nothing.
    first = await person.check()
    assert first.status_code == 200 and first.json() == {"state": "ready"}
    device = person.device
    assert device is not None and len(device) == 43
    [cookie] = [c for c in first.headers.get_list("set-cookie") if c.startswith(f"{DEVICE_COOKIE}=")]
    parts = [part.strip() for part in cookie.split(";")]
    assert {"httponly", "samesite=lax", "path=/"} <= {part.lower() for part in parts}
    [expires] = [part.split("=", 1)[1] for part in parts if part.lower().startswith("expires=")]
    lifetime = parsedate_to_datetime(expires) - await world.factory.now()
    assert timedelta(days=179) < lifetime < timedelta(days=180, minutes=1)  # DEVICE_COOKIE_DAYS
    assert (await person.check()).json() == {"state": "ready"}
    assert person.device == device  # kept, never replaced
    row = await binding(world, person.info.id)
    assert (row.link_device_hash, row.activated_at is not None) == (None, True)
    assert await sessions(world, person.info.id) == []

    # Entered: this browser is signed in, and every other browser finds the link in use.
    assert (await person.enter()).status_code == 200
    assert (await person.check()).json() == {"state": "signed_in"}
    other = world.browser(person.info)
    assert (await other.check()).json() == {"state": "in_use"}
    assert other.device is not None and other.device != device

    # Signed out: the same browser may enter again, nobody else.
    assert (await person.logout()).status_code == 200
    assert person.token is None and person.device == device
    assert (await person.check()).json() == {"state": "ready"}
    assert (await other.check()).json() == {"state": "in_use"}

    # Unknown, malformed and example links are simply invalid (a page state, not an error).
    for token in (new_token(), "example", "not a token", "", "x" * 100):
        resp = await other.check(token)
        assert resp.status_code == 200 and resp.json() == {"state": "invalid"}, token
    assert_error(await other.check("x" * 101), 422, "VALIDATION_FAILED")

    await world.factory.execute(
        "UPDATE participants SET disabled_at = clock_timestamp() WHERE id = :p", p=person.info.id
    )
    assert (await person.check()).json() == {"state": "disabled"}
    assert (await other.check()).json() == {"state": "disabled"}

    # No check wrote anything: one binding and one sign-in, both from the tap.
    assert await audited(world, person.info.id) == ["participant.link_bound", "participant.login"]
    assert len(await sessions(world, person.info.id)) == 1


async def test_the_first_browser_to_enter_keeps_the_link(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    event = await world.live_event()
    info = await world.factory.create_participant(world.programme_id)
    # The same link opened on four phones: every one of them may still try.
    devices = [world.browser(info) for _ in range(4)]
    for device in devices:
        assert (await device.check()).json() == {"state": "ready"}
    # Release all four taps together at the participant row lock.
    barrier = asyncio.Barrier(len(devices))
    original_lock = auth_service.lock_participant

    async def lock_together(db: Any, participant_id: uuid.UUID) -> Any:
        await asyncio.wait_for(barrier.wait(), timeout=10)
        return await original_lock(db, participant_id)

    monkeypatch.setattr(auth_service, "lock_participant", lock_together)
    responses = await asyncio.gather(*(d.enter() for d in devices))
    monkeypatch.setattr(auth_service, "lock_participant", original_lock)

    winners = [d for d, r in zip(devices, responses, strict=True) if r.status_code == 200]
    assert len(winners) == 1, [r.text for r in responses]
    [owner] = winners
    for device, resp in zip(devices, responses, strict=True):
        if device is not owner:
            error = assert_error(resp, 403, "LINK_IN_USE")
            assert "already in use on another phone or browser" in error["message"]
            assert device.token is None
            assert_error(await device.snapshot(), 401, "UNAUTHENTICATED")
            assert (await device.check()).json() == {"state": "in_use"}
    row = await binding(world, info.id)
    assert row.link_device_hash == token_hash(owner.device or "") and row.link_bound_at is not None
    [session] = await sessions(world, info.id)
    assert session.revoked_at is None and session.token_hash == token_hash(owner.token or "")
    assert sorted(await audited(world, info.id)) == sorted(
        ["participant.link_bound", "participant.login"] + ["participant.link_refused"] * 3
    )
    reasons = await world.factory.fetch(
        "SELECT details->>'reason' AS reason FROM audit_log WHERE actor_id = :p "
        "AND action = 'participant.link_refused'",
        p=info.id,
    )
    assert {r.reason for r in reasons} == {"other_device"}
    # Refusals are audited for the help desk but never counted against the venue's address.
    assert await failures(world) == 0

    # The owner opening the link again (another tab, the email again) keeps its session.
    token = owner.token
    again = await owner.enter()
    assert again.status_code == 200 and again.json() == {"ok": True, "reused_session": True}
    assert owner.token == token and len(await sessions(world, info.id)) == 1
    assert (await owner.vote(event.ballot.id, event.ballot.nominee_ids[0])).status_code == 201


async def test_the_same_browser_comes_back_after_signing_out_or_expiring(world: World) -> None:
    phone = await world.participant()
    old_token = phone.token
    assert (await phone.logout()).status_code == 200
    assert phone.token is None and phone.device is not None  # signing out keeps the device cookie

    stale = world.browser(phone.info)
    stale.http.cookies.set("sv_session", old_token or "")
    error = assert_error(await stale.snapshot(), 401, "SESSION_REVOKED")
    assert error["details"]["reason"] == "logout"
    assert error["message"] == REVOKE_MESSAGES["logout"]

    resp = await phone.enter()
    assert resp.status_code == 200 and resp.json() == {"ok": True, "reused_session": False}
    assert_error(await world.browser(phone.info).enter(), 403, "LINK_IN_USE")
    old, current = await sessions(world, phone.info.id)
    assert (old.revoke_reason, current.revoked_at) == ("logout", None)

    # Expired: the next entry from the same browser ends it and opens a new session.
    await world.factory.execute(
        "UPDATE participant_sessions SET expires_at = clock_timestamp() - interval '1 second' WHERE id = :s",
        s=current.id,
    )
    error = assert_error(await phone.snapshot(), 401, "SESSION_EXPIRED")
    assert error["message"] == REVOKE_MESSAGES["expired"]
    assert (await phone.check()).json() == {"state": "ready"}
    resp = await phone.enter()
    assert resp.status_code == 200 and resp.json()["reused_session"] is False
    rows = {r.id: r for r in await sessions(world, phone.info.id)}
    assert (rows[current.id].revoke_reason, rows[current.id].revoked_at is not None) == ("expired", True)
    assert sum(1 for r in rows.values() if r.revoked_at is None) == 1
    assert (await phone.snapshot()).status_code == 200


async def test_organiser_reset_signs_out_and_keeps_the_link_on_its_browser(world: World) -> None:
    phone = await world.participant()
    bound = (await binding(world, phone.info.id)).link_device_hash
    admin = await world.admin()
    resp = await admin.post(f"/participants/{phone.info.id}/reset-session")
    assert resp.json() == {"ok": True, "sessions_revoked": 1}
    error = assert_error(await phone.snapshot(), 401, "SESSION_REVOKED")
    assert error["details"]["reason"] == "admin_reset"
    assert error["message"] == REVOKE_MESSAGES["admin_reset"]

    # "Sign out everywhere" keeps the binding: only a new invitation moves the link.
    assert (await binding(world, phone.info.id)).link_device_hash == bound
    assert_error(await world.browser(phone.info).enter(), 403, "LINK_IN_USE")
    assert (await phone.enter()).status_code == 200
    detail = (await admin.get(f"/participants/{phone.info.id}")).json()["participant"]
    assert detail["link_bound_at"] is not None and detail["link_issued_at"] is not None
    assert not {"link_token_hash", "link_device_hash"} & set(detail)


@pytest.mark.parametrize("action", ["admin_reset", "disabled", "credentials_reissued"])
async def test_revocation_ends_the_old_session_and_allows_a_new_sign_in(world: World, action: str) -> None:
    event = await world.live_event(ballots=2)
    voter = await world.participant()
    in_flight = await live_context(world, voter)  # authenticated before the revocation
    admin = await world.admin()
    first, second = event.ballots

    if action == "admin_reset":
        resp = await admin.post(f"/participants/{voter.info.id}/reset-session")
        assert resp.json() == {"ok": True, "sessions_revoked": 1}
    elif action == "disabled":
        resp = await admin.post(f"/participants/{voter.info.id}/disable", {"reason": "lost phone"})
        assert resp.status_code == 200, resp.text
    else:
        # The organiser re-sends the invitation: a new link, and the old session ends.
        resp = await admin.post(f"/participants/{voter.info.id}/invite")
        assert resp.status_code == 200, resp.text
        job = await world.factory.fetchrow(
            "SELECT secret_ciphertext FROM email_jobs WHERE participant_id = :p AND kind = 'invitation' "
            "ORDER BY created_at DESC LIMIT 1",
            p=voter.info.id,
        )
        new_link = secret_of(job.secret_ciphertext)

    error = assert_error(await voter.snapshot(), 401, "SESSION_REVOKED")
    assert error["details"]["reason"] == action
    assert error["message"] == REVOKE_MESSAGES[action]
    assert_error(await voter.vote(first.id, first.nominee_ids[0]), 401, "SESSION_REVOKED")
    # A request that passed authentication just before the revocation is refused by the
    # re-check inside the vote transaction.
    async with sessionmaker()() as db:
        with pytest.raises(AppError) as caught:
            await voting.submit_vote(db, in_flight, second.id, second.nominee_ids[0], "in-flight")
    assert caught.value.code == "SESSION_REVOKED"
    assert (
        await world.factory.fetchval("SELECT count(*) FROM votes WHERE participant_id = :p", p=voter.info.id)
        == 0
    )

    if action == "disabled":
        # Disabled is decided before the device: every browser is told so, even the bound one.
        assert_error(await world.browser(voter.info).enter(), 403, "ACCOUNT_DISABLED")
        assert_error(await voter.enter(), 403, "ACCOUNT_DISABLED")
        assert (await admin.post(f"/participants/{voter.info.id}/enable")).status_code == 200
    if action == "credentials_reissued":
        # The old link is dead everywhere; the new one is free for whichever browser opens it.
        assert_error(await voter.enter(), 401, "LINK_INVALID")
        browser = world.browser(voter.info)
        resp = await browser.enter(new_link)
    else:
        browser = voter
        resp = await voter.enter()
    assert resp.status_code == 200, resp.text
    assert (await browser.vote(first.id, first.nominee_ids[0])).status_code == 201


class DropLinkResponse(httpx.AsyncBaseTransport):
    """Delivers the sign-in to the server, then loses the response (like a dropped connection)."""

    def __init__(self, inner: httpx.AsyncBaseTransport) -> None:
        self.inner = inner
        self.dropped = 0

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        response = await self.inner.handle_async_request(request)
        if request.method == "POST" and request.url.path == f"{API}/auth/link" and not self.dropped:
            await response.aread()  # the server has finished and committed
            self.dropped += 1
            raise httpx.ReadError("connection reset before the response arrived", request=request)
        return response


async def test_a_first_tap_whose_response_is_lost_can_be_retried(world: World) -> None:
    info = await world.factory.create_participant(world.programme_id)
    transport = DropLinkResponse(httpx.ASGITransport(app=world.app, client=(world.ip, 51000)))
    phone = ParticipantClient(world.http(transport=transport), info)
    # Opening the link hands the browser its device cookie before any tap.
    assert (await phone.check()).json() == {"state": "ready"}
    with pytest.raises(httpx.ReadError):
        await phone.enter()
    assert transport.dropped == 1 and phone.token is None
    # The server did bind the link and open a session that never reached the browser.
    assert (await binding(world, info.id)).link_device_hash == token_hash(phone.device or "")
    [lost] = await sessions(world, info.id)

    retry = await phone.enter()
    assert retry.status_code == 200, retry.text
    assert retry.json() == {"ok": True, "reused_session": False}
    rows = {r.id: r for r in await sessions(world, info.id)}
    assert rows[lost.id].revoke_reason == "replaced"
    [live] = [r for r in rows.values() if r.revoked_at is None]
    assert live.token_hash == token_hash(phone.token or "")
    assert (await phone.snapshot()).status_code == 200
    assert "participant.link_refused" not in await audited(world, info.id)


async def test_disabled_account_is_refused_even_with_a_live_session(world: World) -> None:
    event = await world.live_event()
    voter = await world.participant()
    in_flight = await live_context(world, voter)
    # Disabled without revoking (e.g. an operator fixing data by hand).
    await world.factory.execute(
        "UPDATE participants SET disabled_at = clock_timestamp() WHERE id = :p", p=voter.info.id
    )
    assert_error(await voter.snapshot(), 403, "ACCOUNT_DISABLED")
    assert_error(await voter.vote(event.ballot.id, event.ballot.nominee_ids[0]), 403, "ACCOUNT_DISABLED")
    async with sessionmaker()() as db:
        with pytest.raises(AppError) as caught:
            await voting.submit_vote(db, in_flight, event.ballot.id, event.ballot.nominee_ids[0], "k")
    assert caught.value.code == "ACCOUNT_DISABLED"
