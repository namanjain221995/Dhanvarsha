"""Invitations create the sign-in: the email address is the user ID and the email carries
the password. No activation step, sign-up or self-service reset exists."""

from __future__ import annotations

import re
import uuid
from datetime import timedelta
from typing import Any

import httpx

from helpers import API, Factory, World, assert_error, secret_of
from stagevote.security import verify_password_sync

PASSWORD = re.compile(r"[ABCDEFGHJKMNPQRSTUVWXYZ2-9]{4}(-[ABCDEFGHJKMNPQRSTUVWXYZ2-9]{4}){2}")


async def invite(world: World, participant_id: uuid.UUID) -> httpx.Response:
    resp = await (await world.admin()).post(f"/participants/{participant_id}/invite")
    assert resp.status_code == 200, resp.text
    return resp


async def invitation_jobs(factory: Factory, participant_id: uuid.UUID) -> list[Any]:
    return await factory.fetch(
        "SELECT * FROM email_jobs WHERE participant_id = :p AND kind = 'invitation' ORDER BY created_at, id",
        p=participant_id,
    )


async def emailed_password(factory: Factory, participant_id: uuid.UUID) -> str:
    return secret_of((await invitation_jobs(factory, participant_id))[-1].secret_ciphertext, "password")


async def session_count(factory: Factory, participant_id: uuid.UUID) -> int:
    return int(
        await factory.fetchval(
            "SELECT count(*) FROM participant_sessions WHERE participant_id = :p", p=participant_id
        )
    )


async def test_invitation_sets_the_password_and_keeps_only_an_encrypted_copy(world: World) -> None:
    person = await world.participant(login=False, activated=False)
    await invite(world, person.info.id)

    [job] = await invitation_jobs(world.factory, person.info.id)
    assert (job.status, job.recipient, job.attempts) == ("queued", person.info.email, 0)
    password = secret_of(job.secret_ciphertext, "password")
    assert PASSWORD.fullmatch(password)

    # The plaintext exists only as Fernet ciphertext in the job and as an Argon2 hash on the
    # account: never in plain form in any row, the audit log or the outbox.
    assert password not in await world.factory.row_json("email_jobs", job.id)
    assert password.encode() not in bytes(job.secret_ciphertext)
    assert password not in await world.factory.row_json("participants", person.info.id)
    assert password not in await world.factory.programme_text(world.programme_id)
    row = await world.factory.fetchrow("SELECT * FROM participants WHERE id = :p", p=person.info.id)
    assert row.password_hash.startswith("$argon2id$")
    assert verify_password_sync(row.password_hash, password)
    assert row.current_invitation_id == job.invitation_id
    assert row.activated_at is None  # nobody has signed in yet
    assert row.last_invited_at is not None


async def test_email_address_and_emailed_password_sign_in_directly(world: World) -> None:
    person = await world.participant(login=False, activated=False)
    await invite(world, person.info.id)
    password = await emailed_password(world.factory, person.info.id)

    # The email address is the user ID, matched case-insensitively.
    resp = await person.login(password, user_id=person.info.email.upper())
    assert resp.status_code == 200, resp.text
    assert await session_count(world.factory, person.info.id) == 1
    first = await world.factory.fetchval(
        "SELECT activated_at FROM participants WHERE id = :p", p=person.info.id
    )
    assert first is not None  # recorded as the first sign-in
    snapshot = await person.snapshot()
    assert snapshot.status_code == 200, snapshot.text


async def test_not_invited_unknown_and_wrong_password_answer_identically(world: World) -> None:
    """Login cannot be used to learn which addresses are invited."""
    invited = await world.participant(login=False, activated=False)
    await invite(world, invited.info.id)
    not_invited = await world.participant(login=False, activated=False)

    async def attempt(identifier: str) -> dict[str, Any]:
        resp = await invited.http.post(
            f"{API}/auth/login", json={"user_id": identifier, "password": "ABCD-EFGH-JKLM"}
        )
        body = assert_error(resp, 401, "INVALID_CREDENTIALS")
        return {k: body[k] for k in ("code", "message", "details")}

    unknown = await attempt(f"nobody-{uuid.uuid4().hex[:8]}@example.com")
    assert await attempt(invited.info.email) == unknown
    assert await attempt(not_invited.info.email) == unknown
    assert await attempt(invited.info.user_code) == unknown


async def test_resending_replaces_the_password_and_supersedes_the_old_email(world: World) -> None:
    person = await world.participant(login=False, activated=False)
    await invite(world, person.info.id)
    first_password = await emailed_password(world.factory, person.info.id)
    resp = await invite(world, person.info.id)
    assert resp.json() == {"ok": True, "resent": True}
    second_password = await emailed_password(world.factory, person.info.id)
    assert first_password != second_password

    first, second = await invitation_jobs(world.factory, person.info.id)
    assert first.status == "superseded"
    assert first.secret_ciphertext is None and first.secret_purged_at is not None
    assert second.status == "queued" and second.secret_ciphertext is not None
    current = await world.factory.fetchval(
        "SELECT current_invitation_id FROM participants WHERE id = :p", p=person.info.id
    )
    assert current == second.invitation_id != first.invitation_id

    assert_error(await person.login(first_password), 401, "INVALID_CREDENTIALS")
    resp = await person.login(second_password)
    assert resp.status_code == 200, resp.text


async def test_disabled_accounts_cannot_be_invited(world: World) -> None:
    admin = await world.admin()
    person = await world.participant(login=False, activated=False)
    assert (await admin.post(f"/participants/{person.info.id}/disable")).status_code == 200
    assert_error(await admin.post(f"/participants/{person.info.id}/invite"), 409, "ACCOUNT_DISABLED")
    assert await invitation_jobs(world.factory, person.info.id) == []


async def test_invitation_never_bypasses_the_login_window(world: World) -> None:
    await world.set_window(timedelta(hours=1), timedelta(hours=7))
    early = await world.participant(login=False, activated=False)
    await invite(world, early.info.id)
    password = await emailed_password(world.factory, early.info.id)
    assert_error(await early.login(password), 403, "LOGIN_NOT_OPEN")

    await world.set_window(timedelta(hours=-3), timedelta(hours=-1))
    assert_error(await early.login(password), 403, "LOGIN_CLOSED")
    assert await session_count(world.factory, early.info.id) == 0


async def test_retired_self_service_endpoints_do_not_exist(world: World) -> None:
    """No sign-up, activation or password reset is offered to participants."""
    http = world.http()
    for path in ("/auth/activate", "/auth/recovery/request", "/auth/recovery/complete"):
        resp = await http.post(f"{API}{path}", json={})
        assert resp.status_code in (404, 405), (path, resp.status_code)
    admin = await world.admin()
    person = await world.participant(login=False)
    for action in ("reminder", "recovery"):
        resp = await admin.post(f"/participants/{person.info.id}/{action}")
        assert resp.status_code in (404, 405), (action, resp.status_code)


async def sign_in(world: World, user_id: str, password: str) -> httpx.Response:
    return await world.http().post(f"{API}/auth/login", json={"user_id": user_id, "password": password})


async def test_send_to_adds_new_people_and_emails_everyone_a_real_password(world: World) -> None:
    admin = await world.admin()
    existing = await world.participant(login=False, activated=False)
    await invite(world, existing.info.id)
    old_password = await emailed_password(world.factory, existing.info.id)
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
        password = await emailed_password(world.factory, row.id)
        assert PASSWORD.fullmatch(password)
        assert (await sign_in(world, address, password)).status_code == 200

    # Someone invited before gets a new password; the old one stops working.
    new_password = await emailed_password(world.factory, existing.info.id)
    assert new_password != old_password
    assert_error(await sign_in(world, existing.info.email, old_password), 401, "INVALID_CREDENTIALS")
    assert (await sign_in(world, existing.info.email, new_password)).status_code == 200
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
