"""Scheduled live windows, the manual override, resume and the worker's live ticks."""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timedelta
from typing import Any

import pytest
from sqlalchemy.exc import IntegrityError

from helpers import AdminClient, World, assert_error, pg_error, wait_until_db_time
from stagevote.config import get_settings
from stagevote.mailer.providers import RecordingProvider
from stagevote.worker.runner import Worker


async def switch_to_scheduled(world: World) -> AdminClient:
    admin = await world.admin()
    resp = await admin.patch(
        "/programme", {"settings_version": await admin.settings_version(), "run_mode": "scheduled"}
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["run_mode"] == "scheduled"
    return admin


async def planned_event(
    admin: AdminClient, name: str, start: datetime, end: datetime, nominees: int = 2
) -> uuid.UUID:
    resp = await admin.post(
        "/events", {"name": name, "planned_start_at": start.isoformat(), "planned_end_at": end.isoformat()}
    )
    assert resp.status_code == 201, resp.text
    event = resp.json()["event"]
    for i in range(nominees):
        added = await admin.post(
            f"/ballots/{event['ballots'][0]['id']}/nominees", {"name": f"{name} act {i + 1}"}
        )
        assert added.status_code == 201, added.text
    return uuid.UUID(event["id"])


async def jobs(world: World, event_id: uuid.UUID | None = None) -> list[Any]:
    rows = await world.factory.fetch(
        "SELECT * FROM schedule_jobs WHERE programme_id = :p ORDER BY schedule_revision, run_at",
        p=world.programme_id,
    )
    return [r for r in rows if event_id is None or r.event_id == event_id]


async def programme(world: World) -> Any:
    return await world.factory.fetchrow("SELECT * FROM programmes WHERE id = :p", p=world.programme_id)


async def event_row(world: World, event_id: uuid.UUID) -> Any:
    return await world.factory.fetchrow("SELECT * FROM events WHERE id = :e", e=event_id)


async def test_switching_to_scheduled_mode_reports_the_new_live_version(world: World) -> None:
    admin = await world.admin()
    before = await admin.live_version()
    settings_version = await admin.settings_version()
    resp = await admin.patch("/programme", {"settings_version": settings_version, "run_mode": "scheduled"})
    assert resp.status_code == 200, resp.text
    current = await admin.live_version()
    assert current == before + 1  # a run-mode change is a live-state change
    # Admin screens send this value back as expected_live_version: it must be current.
    assert resp.json()["live_version"] == current
    stored = (await admin.get("/programme")).json()
    assert (stored["run_mode"], stored["settings_version"]) == ("scheduled", settings_version + 1)
    assert resp.json()["settings_version"] == settings_version + 1
    # ...and using it works.
    event = await world.event()
    assert (await admin.open_event(event.id, expected=resp.json()["live_version"])).status_code == 200


async def test_due_job_opens_the_event_with_the_planned_end_as_deadline(world: World, worker: Worker) -> None:
    admin = await switch_to_scheduled(world)
    now = await world.factory.now()
    due = await planned_event(admin, "Opening act", now - timedelta(seconds=5), now + timedelta(minutes=10))
    later = await planned_event(admin, "Finale", now + timedelta(minutes=20), now + timedelta(minutes=30))
    revision = (await programme(world)).schedule_revision

    pending = [j for j in await jobs(world) if j.status == "pending"]
    assert sorted(j.event_id for j in pending) == sorted([due, later])
    assert {j.schedule_revision for j in pending} == {revision}
    for job in pending:
        row = await event_row(world, job.event_id)
        assert (job.run_at, job.window_end_at) == (row.planned_start_at, row.planned_end_at)
    # Every schedule edit produced a new revision; older pending jobs went stale.
    assert all(
        j.status == "stale" and j.schedule_revision < revision for j in await jobs(world) if j not in pending
    )

    await worker.schedule_tick()

    opened = await event_row(world, due)
    assert (opened.status, opened.opened_by, opened.opened_schedule_revision) == (
        "open",
        "schedule",
        revision,
    )
    assert opened.voting_deadline_at == opened.planned_end_at
    [done] = [j for j in await jobs(world, due) if j.schedule_revision == revision]
    assert (done.status, done.attempts) == ("done", 1)
    assert [j.status for j in await jobs(world, later) if j.schedule_revision == revision] == ["pending"]
    audit = await world.factory.fetchrow(
        "SELECT actor_type, details FROM audit_log WHERE action = 'event.opened' AND target_id = :e",
        e=str(due),
    )
    assert audit.actor_type == "system" and audit.details["by"] == "schedule"

    voter = await world.participant()
    assert (await voter.snapshot()).json()["live_event_id"] == str(due)


async def test_changing_the_schedule_makes_old_jobs_stale(world: World, worker: Worker) -> None:
    admin = await switch_to_scheduled(world)
    now = await world.factory.now()
    event_id = await planned_event(admin, "Moved round", now + timedelta(hours=1), now + timedelta(hours=2))
    [old] = [j for j in await jobs(world, event_id) if j.status == "pending"]

    version = (await event_row(world, event_id)).version
    new_start, new_end = now + timedelta(hours=3), now + timedelta(hours=4)
    resp = await admin.patch(
        f"/events/{event_id}",
        {
            "version": version,
            "planned_start_at": new_start.isoformat(),
            "planned_end_at": new_end.isoformat(),
        },
    )
    assert resp.status_code == 200, resp.text

    by_id = {j.id: j for j in await jobs(world, event_id)}
    assert by_id[old.id].status == "stale"
    [new] = [j for j in by_id.values() if j.status == "pending"]
    assert new.schedule_revision == old.schedule_revision + 1
    assert (new.run_at, new.window_end_at) == (new_start, new_end)

    # Even if the old job became due, it would be discarded: it belongs to an old revision.
    await world.factory.execute(
        "UPDATE schedule_jobs SET status = 'pending', run_at = clock_timestamp() - interval '1 second' WHERE id = :j",
        j=old.id,
    )
    await worker.schedule_tick()
    assert (
        await world.factory.fetchval("SELECT status FROM schedule_jobs WHERE id = :j", j=old.id)
    ) == "stale"
    assert (await event_row(world, event_id)).status == "upcoming"


async def test_manual_override_skips_jobs_until_the_schedule_is_resumed(world: World, worker: Worker) -> None:
    admin = await switch_to_scheduled(world)
    now = await world.factory.now()
    due = await planned_event(admin, "Due now", now - timedelta(seconds=5), now + timedelta(minutes=10))
    manual = await planned_event(
        admin, "Manual pick", now + timedelta(minutes=30), now + timedelta(minutes=40)
    )

    assert (await admin.open_event(manual)).status_code == 200
    assert (await programme(world)).schedule_suspended is True
    await worker.schedule_tick()
    [skipped] = [j for j in await jobs(world, due) if j.status != "stale"]
    assert (skipped.status, skipped.result_detail) == ("skipped", "Manual override active")
    assert (await event_row(world, due)).status == "upcoming"
    assert (await event_row(world, manual)).status == "open"

    assert (await admin.close_event(manual)).status_code == 200
    assert (await programme(world)).schedule_suspended is True  # closing manually keeps the override

    preview = (await admin.get("/schedule/preview")).json()
    assert {p["event_id"]: p["outcome"] for p in preview["plan"]} == {str(due): "opens_now"}
    revision = (await programme(world)).schedule_revision
    resp = await admin.post("/schedule/resume", {"expected_live_version": preview["live_version"]})
    assert resp.status_code == 200, resp.text
    assert resp.json()["jobs"] == 1
    state = await programme(world)
    assert (state.schedule_suspended, state.schedule_revision) == (False, revision + 1)
    pending = [j for j in await jobs(world) if j.status == "pending"]
    assert [(j.event_id, j.schedule_revision) for j in pending] == [(due, revision + 1)]

    await worker.schedule_tick()
    assert (await event_row(world, due)).status == "open"
    closed = await event_row(world, manual)
    assert (closed.status, closed.close_reason) == ("closed", "manual")  # never reopened


async def test_manual_close_of_a_scheduled_event_suspends_the_schedule(world: World, worker: Worker) -> None:
    admin = await switch_to_scheduled(world)
    now = await world.factory.now()
    boundary = now + timedelta(seconds=1.5)
    opener = await planned_event(admin, "Scheduled opener", now - timedelta(seconds=5), boundary)
    follower = await planned_event(admin, "Follower", boundary, now + timedelta(minutes=10))
    await worker.schedule_tick()
    assert (await event_row(world, opener)).status == "open"
    assert (await programme(world)).schedule_suspended is False

    assert (await admin.close_event(opener)).status_code == 200
    state = await programme(world)
    assert state.schedule_suspended is True and state.schedule_suspended_at is not None
    details = await world.factory.fetchval(
        "SELECT details FROM audit_log WHERE action = 'event.closed' AND target_id = :e", e=str(opener)
    )
    assert details["schedule_suspended"] is True

    await wait_until_db_time(world.factory, boundary)
    await worker.schedule_tick()
    [job] = [j for j in await jobs(world, follower) if j.status != "stale"]
    assert (job.status, job.result_detail) == ("skipped", "Manual override active")
    assert (await event_row(world, follower)).status == "upcoming"


async def test_two_workers_open_a_due_event_exactly_once(world: World, worker: Worker) -> None:
    admin = await switch_to_scheduled(world)
    now = await world.factory.now()
    due = await planned_event(admin, "Contended", now - timedelta(seconds=5), now + timedelta(minutes=10))
    others = [Worker(get_settings(), provider=RecordingProvider()) for _ in range(2)]
    await asyncio.gather(worker.schedule_tick(), *(w.schedule_tick() for w in others))

    assert (await event_row(world, due)).status == "open"
    [done] = [j for j in await jobs(world, due) if j.status != "stale"]
    assert (done.status, done.attempts) == ("done", 1)
    assert (
        await world.factory.fetchval(
            "SELECT count(*) FROM audit_log WHERE action = 'event.opened' AND target_id = :e", e=str(due)
        )
        == 1
    )


async def test_replayed_or_outdated_jobs_never_reopen_or_open_events(world: World, worker: Worker) -> None:
    admin = await switch_to_scheduled(world)
    now = await world.factory.now()
    opener = await planned_event(admin, "Opener", now - timedelta(seconds=5), now + timedelta(minutes=10))
    await worker.schedule_tick()
    [done] = [j for j in await jobs(world, opener) if j.status == "done"]

    # Close it through its deadline (not manually, so the schedule stays active).
    await world.factory.execute(
        "UPDATE events SET voting_deadline_at = opened_at + interval '1 millisecond' WHERE id = :e", e=opener
    )
    await worker.schedule_tick()
    closed = await event_row(world, opener)
    assert (closed.status, closed.close_reason) == ("closed", "deadline")

    # A worker replaying the same job (crash between send and commit, duplicate delivery).
    await world.factory.execute(
        "UPDATE schedule_jobs SET status = 'pending', executed_at = NULL WHERE id = :j", j=done.id
    )
    await worker.schedule_tick()
    replayed = await world.factory.fetchrow(
        "SELECT status, result_detail FROM schedule_jobs WHERE id = :j", j=done.id
    )
    assert (replayed.status, replayed.result_detail) == ("skipped", "Event is already closed")
    after = await event_row(world, opener)
    assert (after.status, after.closed_at) == ("closed", closed.closed_at)
    assert (
        await world.factory.fetchval(
            "SELECT count(*) FROM audit_log WHERE action = 'event.opened' AND target_id = :e", e=str(opener)
        )
        == 1
    )

    # A duplicate job for the same transition and revision cannot even be stored.
    with pytest.raises(IntegrityError) as caught:
        await world.factory.execute(
            "INSERT INTO schedule_jobs (id, programme_id, event_id, action, run_at, window_end_at, schedule_revision, "
            "status, attempts) SELECT :id, programme_id, event_id, action, run_at, window_end_at, schedule_revision, "
            "'pending', 0 FROM schedule_jobs WHERE id = :j",
            id=uuid.uuid4(),
            j=done.id,
        )
    assert pg_error(caught.value).constraint_name == "uq_schedule_jobs_transition"

    # A job left over from an older schedule revision is discarded, not executed.
    later = await planned_event(admin, "Later", now + timedelta(hours=1), now + timedelta(hours=2))
    revision = (await programme(world)).schedule_revision
    planned = await event_row(world, later)
    leftover = uuid.uuid4()
    await world.factory.execute(
        "INSERT INTO schedule_jobs (id, programme_id, event_id, action, run_at, window_end_at, schedule_revision, "
        "status, attempts) VALUES (:id, :p, :e, 'open', clock_timestamp() - interval '1 second', :end, :rev, "
        "'pending', 0)",
        id=leftover,
        p=world.programme_id,
        e=later,
        end=planned.planned_end_at,
        rev=revision - 1,
    )
    await worker.schedule_tick()
    assert (
        await world.factory.fetchval("SELECT status FROM schedule_jobs WHERE id = :j", j=leftover) == "stale"
    )
    assert (await event_row(world, later)).status == "upcoming"


async def test_worker_restarted_after_the_window_marks_the_job_missed(world: World) -> None:
    admin = await switch_to_scheduled(world)
    now = await world.factory.now()
    event_id = await planned_event(
        admin, "Short slot", now - timedelta(seconds=1), now + timedelta(seconds=2)
    )
    [job] = [j for j in await jobs(world, event_id) if j.status == "pending"]
    live_before = await admin.live_version()

    # No worker ran during the window. A freshly started worker picks the job up late.
    await wait_until_db_time(world.factory, job.window_end_at)
    restarted = Worker(get_settings(), provider=RecordingProvider())
    await restarted.schedule_tick()

    missed = await world.factory.fetchrow(
        "SELECT status, result_detail FROM schedule_jobs WHERE id = :j", j=job.id
    )
    assert missed.status == "missed"
    assert "window ended" in missed.result_detail
    row = await event_row(world, event_id)
    assert (row.status, row.opened_at) == ("upcoming", None)
    audit = await world.factory.fetchrow(
        "SELECT actor_type, details FROM audit_log WHERE action = 'schedule.missed' AND target_id = :e",
        e=str(event_id),
    )
    assert audit.actor_type == "system"
    assert datetime.fromisoformat(audit.details["planned_end"]) == job.window_end_at
    assert await admin.live_version() == live_before + 1  # admin screens are told


async def test_deadline_sweep_closes_open_events_past_their_deadline(world: World, worker: Worker) -> None:
    event = await world.live_event()
    voter = await world.participant()
    [(deadline,)] = await world.factory.execute(
        "UPDATE events SET voting_deadline_at = clock_timestamp() + interval '1 second' WHERE id = :e "
        "RETURNING voting_deadline_at",
        e=event.id,
    )
    await world.factory.touch(world.programme_id)
    await wait_until_db_time(world.factory, deadline)

    assert (await event_row(world, event.id)).status == "open"  # only the stored status lags
    assert_error(await voter.vote(event.ballot.id, event.ballot.nominee_ids[0]), 409, "VOTING_CLOSED")
    admin = await world.admin()
    live_before = await admin.live_version()

    await worker.schedule_tick()

    row = await event_row(world, event.id)
    assert (row.status, row.closed_at, row.close_reason) == ("closed", deadline, "deadline")
    audit = await world.factory.fetchrow(
        "SELECT actor_type, details FROM audit_log WHERE action = 'event.closed' AND target_id = :e",
        e=str(event.id),
    )
    assert (audit.actor_type, audit.details) == ("system", {"reason": "deadline"})
    assert await admin.live_version() == live_before + 1
    await worker.schedule_tick()  # idempotent
    assert await admin.live_version() == live_before + 1


async def test_overlapping_planned_windows_are_rejected(world: World) -> None:
    admin = await world.admin()
    now = await world.factory.now()
    hour = timedelta(hours=1)

    first = await planned_event(admin, "First", now + hour, now + 2 * hour)
    resp = await admin.post(
        "/events",
        {
            "name": "Clash",
            "planned_start_at": (now + 1.5 * hour).isoformat(),
            "planned_end_at": (now + 2.5 * hour).isoformat(),
        },
    )
    assert "overlaps" in assert_error(resp, 422, "VALIDATION_FAILED")["message"]
    # Windows are half-open: one may start exactly when the previous ends.
    adjacent = await planned_event(admin, "Adjacent", now + 2 * hour, now + 3 * hour)
    resp = await admin.patch(
        f"/events/{adjacent}",
        {
            "version": (await event_row(world, adjacent)).version,
            "planned_start_at": (now + 1.75 * hour).isoformat(),
            "planned_end_at": (now + 2.75 * hour).isoformat(),
        },
    )
    assert_error(resp, 422, "VALIDATION_FAILED")

    # The database refuses the overlap even when the API is bypassed.
    with pytest.raises(IntegrityError) as caught:
        await world.factory.create_event(
            world.programme_id, name="Sneaky", planned_start=now + 1.25 * hour, planned_end=now + 1.5 * hour
        )
    error = pg_error(caught.value)
    assert (error.sqlstate, error.constraint_name) == ("23P01", "ex_events_planned_window_overlap")

    # Archived events free their window; restoring one into a clash is refused cleanly.
    assert (await admin.post(f"/events/{first}/archive")).status_code == 200
    await planned_event(admin, "Replacement", now + hour, now + 1.5 * hour)
    resp = await admin.post(f"/events/{first}/unarchive")
    assert_error(resp, 422, "VALIDATION_FAILED")
    assert (await event_row(world, first)).archived_at is not None
