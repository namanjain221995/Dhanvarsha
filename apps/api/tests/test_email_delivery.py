"""Durable email delivery: outcome classification, retries, leases and secret handling."""

from __future__ import annotations

import asyncio
import logging
import socket
import time
import uuid
from datetime import timedelta
from typing import Any

import httpx
import pytest
from jinja2 import UndefinedError

from helpers import (
    API,
    ParticipantClient,
    World,
    assert_error,
    drain,
    dumps,
    eventually,
    mailpit_api,
    secret_of,
)
from stagevote.config import get_settings
from stagevote.db import sessionmaker
from stagevote.logging import JsonFormatter
from stagevote.mailer import delivery
from stagevote.mailer.delivery import lease_jobs, process_job
from stagevote.mailer.providers import (
    Check,
    OutgoingEmail,
    RecordingProvider,
    SendResult,
    SmtpProvider,
    make_provider,
)
from stagevote.security import encrypt_payload
from stagevote.services.invitations import sign_in_link
from stagevote.worker.runner import Worker


async def invite(world: World, person: ParticipantClient) -> tuple[uuid.UUID, str]:
    """Send the invitation; returns (job id, the link token it carries)."""
    resp = await (await world.admin()).post(f"/participants/{person.info.id}/invite")
    assert resp.status_code == 200, resp.text
    job = await world.factory.fetchrow(
        "SELECT id, secret_ciphertext FROM email_jobs WHERE participant_id = :p AND status = 'queued' "
        "AND kind = 'invitation'",
        p=person.info.id,
    )
    return job.id, secret_of(job.secret_ciphertext)


async def invited(world: World, **kwargs: Any) -> tuple[ParticipantClient, uuid.UUID, str]:
    person = await world.participant(login=False, activated=False, **kwargs)
    job_id, token = await invite(world, person)
    return person, job_id, token


def link_of(token: str) -> str:
    return sign_in_link(get_settings().app_base_url, token)


async def job(world: World, job_id: uuid.UUID) -> Any:
    return await world.factory.fetchrow("SELECT * FROM email_jobs WHERE id = :j", j=job_id)


async def attempts(world: World, job_id: uuid.UUID) -> list[Any]:
    return await world.factory.fetch(
        "SELECT * FROM email_attempts WHERE job_id = :j ORDER BY attempt_no", j=job_id
    )


async def tick(worker: Worker) -> None:
    await worker.email_tick()
    await drain(worker)


async def make_due(world: World, job_id: uuid.UUID) -> None:
    await world.factory.execute(
        "UPDATE email_jobs SET next_attempt_at = now() - interval '1 second' WHERE id = :j", j=job_id
    )


async def test_transient_error_requeues_with_backoff_and_records_the_attempt(
    world: World, worker: Worker, provider: RecordingProvider
) -> None:
    provider.script = [
        SendResult("transient_error", error_code="smtp_421", error_detail="rcpt: 421 try later")
    ]
    person, job_id, token = await invited(world)

    await tick(worker)
    queued = await job(world, job_id)
    assert (queued.status, queued.attempts, queued.last_error_code, queued.lease_owner) == (
        "queued",
        1,
        "smtp_421",
        None,
    )
    assert queued.next_attempt_at > await world.factory.now() + timedelta(seconds=10)  # backed off
    assert queued.secret_ciphertext is not None  # the same credential will be resent
    [first] = await attempts(world, job_id)
    assert (first.attempt_no, first.outcome, first.error_code) == (1, "transient_error", "smtp_421")
    assert first.finished_at is not None

    await tick(worker)  # not due yet: nothing happens
    assert (await job(world, job_id)).attempts == 1 and provider.sent == []

    await make_due(world, job_id)
    await tick(worker)
    accepted = await job(world, job_id)
    assert (accepted.status, accepted.attempts, accepted.provider_message_id) == ("accepted", 2, "rec-0")
    assert accepted.secret_ciphertext is None and accepted.secret_purged_at is not None
    assert [a.outcome for a in await attempts(world, job_id)] == ["transient_error", "accepted"]
    [message] = provider.sent
    assert message.to == person.info.email
    assert f"Enter the arena: {link_of(token)}" in message.text


async def test_permanent_error_fails_without_a_retry_loop(
    world: World, worker: Worker, provider: RecordingProvider
) -> None:
    provider.script = [
        SendResult("permanent_error", error_code="smtp_550", error_detail="rcpt: 550 no such user")
    ]
    _, job_id, _ = await invited(world)
    await tick(worker)
    for _ in range(3):
        await make_due(world, job_id)
        await tick(worker)
    failed = await job(world, job_id)
    assert (failed.status, failed.attempts, failed.last_error_code) == ("failed", 1, "smtp_550")
    assert failed.finished_at is not None
    assert len(await attempts(world, job_id)) == 1
    assert provider.sent == []

    # Transient errors stop too, once the attempt budget is spent.
    provider.script = [SendResult("transient_error", error_code="smtp_451")] * 5
    _, capped, _ = await invited(world)
    await world.factory.execute("UPDATE email_jobs SET max_attempts = 2 WHERE id = :j", j=capped)
    for _ in range(4):
        await make_due(world, capped)
        await tick(worker)
    exhausted = await job(world, capped)
    assert (exhausted.status, exhausted.attempts) == ("failed", 2)
    assert len(provider.script) == 3


async def test_configuration_error_pauses_the_email_loop(
    world: World, worker: Worker, provider: RecordingProvider
) -> None:
    provider.script = [
        SendResult(
            "permanent_error",
            error_code="smtp_auth_failed",
            error_detail="535 authentication failed",
            config_error=True,
        )
    ]
    _, first, _ = await invited(world)
    await tick(worker)
    assert (await job(world, first)).status == "failed"
    assert worker.email_paused_until > time.time() + 60

    _, second, _ = await invited(world)
    await tick(worker)
    waiting = await job(world, second)
    assert (waiting.status, waiting.attempts, waiting.lease_owner) == ("queued", 0, None)
    assert provider.sent == []

    await worker.heartbeat()
    status = (await (await world.admin()).get("/email/status")).json()
    assert status["worker"]["paused_until"] is not None
    assert status["recent_config_error"]["last_error_code"] == "smtp_auth_failed"


async def test_unknown_outcome_is_never_retried_without_admin_confirmation(
    world: World, worker: Worker, provider: RecordingProvider
) -> None:
    provider.script = [
        SendResult("unknown", error_code="smtp_data_interrupted", error_detail="data: TimeoutError")
    ]
    _, job_id, token = await invited(world)
    await tick(worker)
    for _ in range(2):
        await make_due(world, job_id)
        await tick(worker)
        await worker.maintenance_tick()
    unknown = await job(world, job_id)
    assert (unknown.status, unknown.attempts, unknown.last_error_code) == (
        "unknown",
        1,
        "smtp_data_interrupted",
    )
    assert unknown.secret_ciphertext is not None  # kept for a deliberate retry of the same message

    admin = await world.admin()
    # The bulk "retry failed" action never touches deliveries with an unknown outcome.
    assert (await admin.post("/email/jobs/retry-failed")).json() == {"ok": True, "requeued": 0}
    assert_error(await admin.post(f"/email/jobs/{job_id}/retry"), 409, "POSSIBLE_DUPLICATE")
    assert (await job(world, job_id)).status == "unknown"
    resp = await admin.post(f"/email/jobs/{job_id}/retry", {"confirm_possible_duplicate": True})
    assert resp.status_code == 200, resp.text
    await tick(worker)
    done = await job(world, job_id)
    assert (done.status, done.attempts) == ("accepted", 2)
    # The possible duplicate is the *same* message: same Message-ID, same link.
    assert [m.message_id for m in provider.sent] == [done.message_id, done.message_id]
    assert all(link_of(token) in m.text for m in provider.sent)
    assert (
        await world.factory.fetchval(
            "SELECT details->>'previous_status' FROM audit_log WHERE action = 'email.retried' AND target_id = :j",
            j=str(job_id),
        )
        == "unknown"
    )


class ExplodingProvider:
    name = "exploding"

    async def send(self, message: OutgoingEmail) -> SendResult:
        raise ConnectionResetError("connection reset during DATA")

    async def diagnose(self) -> list[Check]:
        return []


async def test_provider_crash_is_classified_unknown(world: World) -> None:
    _, job_id, _ = await invited(world)
    exploding = Worker(get_settings(), provider=ExplodingProvider())
    await tick(exploding)
    row = await job(world, job_id)
    assert (row.status, row.last_error_code) == ("unknown", "provider_exception")


class StallingProvider:
    """Hangs inside send(): the worker is then killed mid-delivery."""

    name = "stalling"

    def __init__(self) -> None:
        self.started = asyncio.Event()

    async def send(self, message: OutgoingEmail) -> SendResult:
        self.started.set()
        await asyncio.Event().wait()
        raise AssertionError("unreachable")

    async def diagnose(self) -> list[Check]:
        return []


async def test_expired_lease_after_a_started_attempt_becomes_unknown(
    world: World, worker: Worker, provider: RecordingProvider
) -> None:
    _, job_id, _ = await invited(world)
    stalling = StallingProvider()
    doomed = Worker(get_settings(), provider=stalling)
    await doomed.email_tick()
    await asyncio.wait_for(stalling.started.wait(), timeout=10)
    for task in list(doomed.in_flight):  # the worker process dies mid-send
        task.cancel()
    await asyncio.gather(*doomed.in_flight, return_exceptions=True)

    stuck = await job(world, job_id)
    assert (stuck.status, stuck.lease_owner) == ("processing", doomed.worker_id)
    [attempt] = await attempts(world, job_id)
    assert attempt.finished_at is None  # committed before the provider was called

    await world.factory.execute(
        "UPDATE email_jobs SET lease_expires_at = now() - interval '1 second' WHERE id = :j", j=job_id
    )
    await worker.maintenance_tick()
    recovered = await job(world, job_id)
    assert (recovered.status, recovered.last_error_code, recovered.lease_owner) == (
        "unknown",
        "worker_interrupted",
        None,
    )
    assert recovered.secret_ciphertext is not None
    [attempt] = await attempts(world, job_id)
    assert (attempt.outcome, attempt.error_code) == ("unknown", "worker_interrupted")
    await tick(worker)
    assert provider.sent == []  # never resent automatically


async def test_expired_lease_before_sending_is_requeued(
    world: World, worker: Worker, provider: RecordingProvider
) -> None:
    _, job_id, _ = await invited(world)
    async with sessionmaker()() as db:
        assert job_id in await lease_jobs(db, "vanished-worker", 10)
    leased = await job(world, job_id)
    assert (leased.status, leased.attempts, leased.lease_owner) == ("processing", 1, "vanished-worker")
    assert await attempts(world, job_id) == []

    await world.factory.execute(
        "UPDATE email_jobs SET lease_expires_at = now() - interval '1 second' WHERE id = :j", j=job_id
    )
    await worker.maintenance_tick()
    requeued = await job(world, job_id)
    assert (requeued.status, requeued.attempts, requeued.lease_owner) == ("queued", 0, None)
    await tick(worker)
    sent = await job(world, job_id)
    assert (sent.status, sent.attempts) == ("accepted", 1)
    assert len(provider.sent) == 1


async def test_credential_is_purged_after_acceptance_and_never_logged(
    world: World,
    worker: Worker,
    provider: RecordingProvider,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)
    person, job_id, token = await invited(world)
    await tick(worker)
    accepted = await job(world, job_id)
    assert accepted.status == "accepted" and accepted.accepted_at is not None
    assert accepted.secret_ciphertext is None and accepted.secret_purged_at is not None
    [message] = provider.sent
    link = link_of(token)
    assert link in message.text and f'href="{link}"' in message.html  # the email itself carries it

    # Requests carrying the token, including rejected ones, never echo it back.
    near_miss = token[:-1] + ("A" if token[-1] != "A" else "B")
    wrong = await person.http.post(f"{API}/auth/link", json={"token": near_miss})
    assert_error(wrong, 401, "LINK_INVALID")
    malformed = await person.http.post(f"{API}/auth/link", json={"token": token, "note": token})
    assert_error(malformed, 422, "VALIDATION_FAILED")
    checked = await person.http.post(f"{API}/auth/link/check", json={"token": token, "note": token})
    assert_error(checked, 422, "VALIDATION_FAILED")
    assert token not in wrong.text + malformed.text + checked.text
    assert (await person.enter(token)).status_code == 200

    # Rendered exactly as the production JSON log handler would write them.
    formatter = JsonFormatter()
    logged = "\n".join(formatter.format(r) + r.getMessage() for r in caplog.records)
    assert caplog.records, "expected request and worker logs"
    assert person.device and person.device not in logged
    assert token not in logged
    assert token not in await world.factory.programme_text(world.programme_id)


async def test_an_invitation_queued_with_a_password_is_never_sent(
    world: World, worker: Worker, provider: RecordingProvider
) -> None:
    """Invitations queued by the release before sign-in links carry a password, which can
    no longer sign anyone in: they fail visibly instead of going out."""
    _, job_id, _ = await invited(world)
    await world.factory.execute(
        "UPDATE email_jobs SET secret_ciphertext = :c WHERE id = :j",
        c=encrypt_payload({"password": "ABCD-EFGH-JKLM"}),
        j=job_id,
    )
    await tick(worker)
    failed = await job(world, job_id)
    assert (failed.status, failed.last_error_code) == ("failed", "retired_payload")
    assert provider.sent == [] and await attempts(world, job_id) == []


async def test_a_template_error_fails_the_job_instead_of_looping(
    world: World, worker: Worker, provider: RecordingProvider, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, job_id, _ = await invited(world)

    def broken(*args: Any, **kwargs: Any) -> Any:
        raise UndefinedError("'link' is undefined")

    monkeypatch.setattr(delivery, "render_invitation", broken)
    await tick(worker)
    failed = await job(world, job_id)
    assert (failed.status, failed.last_error_code, failed.last_error) == (
        "failed",
        "template_error",
        "The email could not be built from its template (UndefinedError); report this to the developers",
    )
    assert failed.secret_ciphertext is not None  # kept: the organiser can retry once it is fixed
    assert provider.sent == [] and await attempts(world, job_id) == []


async def test_superseded_invitations_are_never_sent(
    world: World, worker: Worker, provider: RecordingProvider
) -> None:
    # Queued job: a newer invitation replaces it before the worker gets to it.
    person, queued_job, _ = await invited(world)
    await invite(world, person)
    superseded = await job(world, queued_job)
    assert (superseded.status, superseded.secret_ciphertext) == ("superseded", None)
    await world.factory.execute(
        "UPDATE email_jobs SET status = 'cancelled' WHERE participant_id = :p AND status = 'queued'",
        p=person.info.id,
    )

    # Leased job: the organiser re-sends while the worker holds the lease.
    late, leased_job, _ = await invited(world)
    async with sessionmaker()() as db:
        assert set(await lease_jobs(db, worker.worker_id, 10)) == {leased_job}
    await invite(world, late)
    assert (await job(world, leased_job)).status == "processing"
    assert await process_job(sessionmaker(), leased_job, worker.worker_id, provider, get_settings()) is None
    row = await job(world, leased_job)
    assert (row.status, row.last_error_code, row.secret_ciphertext) == ("superseded", "superseded", None)
    await world.factory.execute(
        "UPDATE email_jobs SET status = 'cancelled' WHERE participant_id = :p AND status = 'queued'",
        p=late.info.id,
    )

    # Leased job replaced by a newer invitation: only the newest link is sent.
    again, old_job, old_token = await invited(world)
    async with sessionmaker()() as db:
        assert set(await lease_jobs(db, worker.worker_id, 10)) == {old_job}
    new_job, new_token = await invite(world, again)
    assert await process_job(sessionmaker(), old_job, worker.worker_id, provider, get_settings()) is None
    assert (await job(world, old_job)).status == "superseded"
    await tick(worker)
    assert (await job(world, new_job)).status == "accepted"
    [message] = provider.sent
    assert link_of(new_token) in message.text and old_token not in message.text + message.html
    for job_id in (queued_job, leased_job, old_job):
        assert await attempts(world, job_id) == []


async def test_expired_credentials_are_failed_and_purged_by_maintenance(
    world: World, worker: Worker, provider: RecordingProvider
) -> None:
    _, job_id, _ = await invited(world)
    await world.factory.execute(
        "UPDATE email_jobs SET secret_expires_at = now() - interval '1 second' WHERE id = :j", j=job_id
    )
    await worker.maintenance_tick()
    expired = await job(world, job_id)
    assert (expired.status, expired.last_error_code) == ("failed", "credential_expired")
    assert expired.secret_ciphertext is None and expired.secret_purged_at is not None
    await tick(worker)
    assert provider.sent == []
    # A retry would send a dead credential: the admin must issue a new invitation instead.
    assert_error(await (await world.admin()).post(f"/email/jobs/{job_id}/retry"), 409, "CREDENTIAL_GONE")


async def test_two_workers_never_send_the_same_job_twice(world: World) -> None:
    job_ids = [(await invited(world))[1] for _ in range(6)]
    providers = [RecordingProvider(), RecordingProvider()]
    workers = [Worker(get_settings(), provider=p) for p in providers]
    for _ in range(3):
        await asyncio.gather(*(w.email_tick() for w in workers))
        for w in workers:
            await drain(w)
    sent = [m.headers["X-StageVote-Job"] for p in providers for m in p.sent]
    assert sorted(sent) == sorted(str(j) for j in job_ids)
    assert all(p.sent for p in providers)  # both workers took part
    rows = await world.factory.fetch(
        "SELECT status, attempts FROM email_jobs WHERE id = ANY(:ids)", ids=job_ids
    )
    assert {(r.status, r.attempts) for r in rows} == {("accepted", 1)}


async def test_admin_status_says_accepted_not_delivered(world: World, worker: Worker) -> None:
    person, job_id, _ = await invited(world)
    await tick(worker)
    admin = await world.admin()

    status = (await admin.get("/email/status")).json()
    assert any("does not prove inbox delivery" in note for note in status["notes"])
    assert status["counts"]["accepted"] >= 1 and "delivered" not in status["counts"]
    listing = (await admin.get("/email/jobs")).json()
    [item] = [i for i in listing["items"] if i["id"] == str(job_id)]
    assert (item["status"], item["credential_available"]) == ("accepted", False)
    assert item["accepted_at"] is not None
    detail = (await admin.get(f"/participants/{person.info.id}")).json()
    assert [j["status"] for j in detail["email_jobs"]] == ["accepted"]
    assert "delivered" not in (dumps(listing) + dumps(detail)).lower()


def _mailpit_available() -> bool:
    settings = get_settings()
    try:
        with socket.create_connection((settings.smtp_host, settings.smtp_port), timeout=2):
            return True
    except OSError:
        return False


@pytest.mark.skipif(not _mailpit_available(), reason="Mailpit SMTP is not reachable")
async def test_invitation_over_real_smtp_arrives_in_mailpit(world: World) -> None:
    settings = get_settings()
    provider = make_provider(settings)
    assert isinstance(provider, SmtpProvider) and settings.smtp_host == "127.0.0.1"
    address = f"mailpit-{uuid.uuid4().hex[:12]}@example.com"
    _, job_id, token = await invited(world, email=address)
    smtp_worker = Worker(settings, provider=provider)
    for _ in range(3):  # a transient local hiccup is requeued by the worker; retry it promptly
        await tick(smtp_worker)
        sent = await job(world, job_id)
        if sent.status != "queued":
            break
        await make_due(world, job_id)
    assert (sent.status, sent.provider) == ("accepted", "mailpit"), sent.last_error

    async with httpx.AsyncClient(base_url=mailpit_api(), timeout=10, trust_env=False) as mailpit:
        found: list[str] = []
        try:

            async def arrived() -> dict[str, Any] | None:
                resp = await mailpit.get("/search", params={"query": f'to:"{address}"'})
                messages = resp.json().get("messages") or []
                return messages[0] if messages else None

            summary = await eventually(arrived, within=10, what="the message in Mailpit")
            found.append(summary["ID"])
            message = (await mailpit.get(f"/message/{summary['ID']}")).json()
            assert [to["Address"] for to in message["To"]] == [address]
            assert message["MessageID"] == sent.message_id.strip("<>")
            text_part = message["Text"]
            assert text_part.strip(), "a plain-text part is required"
            # Compared in the decoded parts: quoted-printable turns "=" into "=3D" on the wire.
            link = link_of(token)
            assert f"Enter the arena: {link}" in text_part
            assert f'href="{link}"' in message["HTML"]
            for part in (text_part, message["HTML"]):
                assert "This button is your personal pass." in part
                assert "User ID" not in part and "Password:" not in part
        finally:
            if found:
                await mailpit.request("DELETE", "/messages", json={"IDs": found})
