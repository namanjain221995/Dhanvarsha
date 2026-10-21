"""Participant sessions: several devices per account, and every way a session ends."""

from __future__ import annotations

import asyncio
import uuid
from typing import Any

import pytest

from helpers import (
    PARTICIPANT_PASSWORD,
    ParticipantClient,
    World,
    assert_error,
    secret_of,
)
from stagevote.db import sessionmaker
from stagevote.deps import ParticipantContext
from stagevote.errors import AppError
from stagevote.services import auth as auth_service
from stagevote.services import voting


async def sessions(world: World, participant_id: uuid.UUID) -> list[Any]:
    return await world.factory.fetch(
        "SELECT * FROM participant_sessions WHERE participant_id = :p ORDER BY created_at", p=participant_id
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


async def test_several_devices_can_be_signed_in_at_once(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    event = await world.live_event()
    info = await world.factory.create_participant(world.programme_id)
    devices = [world.browser(info) for _ in range(4)]
    # Release all four logins together at the participant row lock.
    barrier = asyncio.Barrier(len(devices))
    original_lock = auth_service.lock_participant

    async def lock_together(db: Any, participant_id: uuid.UUID) -> Any:
        await asyncio.wait_for(barrier.wait(), timeout=10)
        return await original_lock(db, participant_id)

    monkeypatch.setattr(auth_service, "lock_participant", lock_together)
    responses = await asyncio.gather(*(d.login() for d in devices))
    monkeypatch.setattr(auth_service, "lock_participant", original_lock)

    assert [r.status_code for r in responses] == [200] * 4, [r.text for r in responses]
    rows = await sessions(world, info.id)
    assert len(rows) == 4 and all(r.revoked_at is None for r in rows)
    assert len({d.token for d in devices}) == 4
    for device in devices:
        assert (await device.snapshot()).status_code == 200

    # The same browser signing in again reuses its session (same cookie, same row).
    first = devices[0]
    token = first.token
    again = await first.login()
    assert again.status_code == 200 and again.json() == {"ok": True, "reused_session": True}
    assert first.token == token
    assert len(await sessions(world, info.id)) == 4

    # Still one vote per ballot: the other devices get the same receipt back.
    ballot = event.ballot
    assert (await devices[1].vote(ballot.id, ballot.nominee_ids[0])).status_code == 201
    later = await devices[2].vote(ballot.id, ballot.nominee_ids[1])
    assert_error(later, 409, "ALREADY_VOTED")
    assert (
        await world.factory.fetchval("SELECT count(*) FROM votes WHERE participant_id = :p", p=info.id) == 1
    )

    # Signing out on one device leaves the others signed in.
    assert (await devices[3].logout()).status_code == 200
    assert (await devices[0].snapshot()).status_code == 200


async def test_organiser_reset_signs_out_every_device(world: World) -> None:
    phone = await world.participant()
    laptop = world.browser(phone.info)
    assert (await laptop.login()).status_code == 200
    admin = await world.admin()
    resp = await admin.post(f"/participants/{phone.info.id}/reset-session")
    assert resp.json() == {"ok": True, "sessions_revoked": 2}
    for device in (phone, laptop):
        error = assert_error(await device.snapshot(), 401, "SESSION_REVOKED")
        assert error["details"]["reason"] == "admin_reset"


async def test_logout_ends_only_that_session(world: World) -> None:
    first = await world.participant()
    old_token = first.token
    assert (await first.logout()).status_code == 200

    stale = world.browser(first.info)
    stale.http.cookies.set("sv_session", old_token or "")
    error = assert_error(await stale.snapshot(), 401, "SESSION_REVOKED")
    assert error["details"]["reason"] == "logout"
    second = world.browser(first.info)
    assert (await second.login()).status_code == 200
    rows = await sessions(world, first.info.id)
    assert [(r.revoke_reason, r.revoked_at is None) for r in rows] == [("logout", False), (None, True)]


async def test_expired_session_is_replaced_on_the_next_login(world: World) -> None:
    first = await world.participant()
    [old] = await sessions(world, first.info.id)
    await world.factory.execute(
        "UPDATE participant_sessions SET expires_at = clock_timestamp() - interval '1 second' WHERE id = :s",
        s=old.id,
    )
    assert_error(await first.snapshot(), 401, "SESSION_EXPIRED")

    second = world.browser(first.info)
    assert (await second.login()).status_code == 200
    rows = {r.id: r for r in await sessions(world, first.info.id)}
    assert (rows[old.id].revoke_reason, rows[old.id].revoked_at is not None) == ("expired", True)
    assert sum(1 for r in rows.values() if r.revoked_at is None) == 1
    assert_error(await first.snapshot(), 401, "SESSION_REVOKED")


@pytest.mark.parametrize("action", ["admin_reset", "disabled", "credentials_reissued"])
async def test_revocation_ends_the_old_session_and_allows_a_new_login(world: World, action: str) -> None:
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
        # The organiser re-sends the invitation: a new password, and the old session ends.
        resp = await admin.post(f"/participants/{voter.info.id}/invite")
        assert resp.status_code == 200, resp.text
        job = await world.factory.fetchrow(
            "SELECT secret_ciphertext FROM email_jobs WHERE participant_id = :p AND kind = 'invitation' "
            "ORDER BY created_at DESC LIMIT 1",
            p=voter.info.id,
        )
        new_password = secret_of(job.secret_ciphertext, "password")

    error = assert_error(await voter.snapshot(), 401, "SESSION_REVOKED")
    assert error["details"]["reason"] == action
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

    fresh = world.browser(voter.info)
    if action == "disabled":
        assert_error(await fresh.login(), 403, "ACCOUNT_DISABLED")
        assert (await admin.post(f"/participants/{voter.info.id}/enable")).status_code == 200
    if action == "credentials_reissued":
        assert_error(await fresh.login(PARTICIPANT_PASSWORD), 401, "INVALID_CREDENTIALS")
    resp = await fresh.login(new_password if action == "credentials_reissued" else PARTICIPANT_PASSWORD)
    assert resp.status_code == 200, resp.text
    assert (await fresh.vote(first.id, first.nominee_ids[0])).status_code == 201


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
