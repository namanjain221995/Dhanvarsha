"""The background worker: email delivery, scheduled transitions, deadline sweeps and
maintenance. Several workers may run at once; all coordination goes through
PostgreSQL row locks (SKIP LOCKED), leases and the programme live lock.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import signal
import socket
import time
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import text

from ..config import Settings, get_settings
from ..db import dispose_engine, init_engine, sessionmaker
from ..logging import configure_logging, get_logger
from ..mailer.delivery import (
    lease_jobs,
    process_job,
    purge_secrets,
    recover_expired_leases,
    release_unstarted,
)
from ..mailer.providers import Provider, make_provider
from ..services.live import run_due_schedule_jobs, sweep_deadlines

log = get_logger("stagevote.worker")
CONFIG_PAUSE_SECONDS = 300


class Worker:
    def __init__(self, settings: Settings, provider: Provider | None = None) -> None:
        self.settings = settings
        self.provider = provider or make_provider(settings)
        self.worker_id = settings.worker_id or f"{socket.gethostname()}-{os.getpid()}-{uuid.uuid4().hex[:6]}"
        self.stopping = asyncio.Event()
        self.started_at = datetime.now(UTC)
        self.email_paused_until: float = 0.0
        self.in_flight: set[asyncio.Task[Any]] = set()
        self.leased_not_started: set[uuid.UUID] = set()
        self._sent_times: list[float] = []

    # ---- loops ----------------------------------------------------------------

    async def _every(self, seconds: float, fn: Any, name: str) -> None:
        while not self.stopping.is_set():
            try:
                await fn()
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("worker task failed", extra={"task": name})
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self.stopping.wait(), timeout=seconds)

    async def schedule_tick(self) -> None:
        async with sessionmaker()() as db:
            closed = await sweep_deadlines(db)
            opened = await run_due_schedule_jobs(db)
        if closed or opened:
            log.info("live transitions", extra={"deadline_closed": closed, "schedule_jobs": opened})

    async def maintenance_tick(self) -> None:
        async with sessionmaker()() as db:
            recovered = await recover_expired_leases(db)
            purged = await purge_secrets(db)
            async with db.begin():
                await db.execute(
                    text("DELETE FROM outbox_events WHERE created_at < now() - interval '1 day'")
                )
                await db.execute(
                    text("DELETE FROM rate_limit_buckets WHERE window_started_at < now() - interval '1 day'")
                )
                await db.execute(
                    text(
                        "UPDATE participant_sessions SET revoked_at = expires_at, revoke_reason = 'expired' "
                        "WHERE revoked_at IS NULL AND expires_at < now() - interval '1 hour'"
                    )
                )
        if recovered or purged:
            log.info("maintenance", extra={"leases_recovered": recovered, "secrets_purged": purged})

    async def heartbeat(self) -> None:
        info = {
            "email_paused_until": datetime.fromtimestamp(self.email_paused_until, UTC).isoformat()
            if self.email_paused_until > time.time()
            else None,
            "provider": self.provider.name,
            "in_flight": len(self.in_flight),
        }
        async with sessionmaker()() as db, db.begin():
            await db.execute(
                text(
                    "INSERT INTO worker_heartbeats (worker_id, started_at, last_seen_at, info) "
                    "VALUES (:w, :s, now(), CAST(:i AS jsonb)) "
                    "ON CONFLICT (worker_id) DO UPDATE SET last_seen_at = now(), info = EXCLUDED.info"
                ),
                {"w": self.worker_id, "s": self.started_at, "i": json.dumps(info)},
            )
            await db.execute(
                text("DELETE FROM worker_heartbeats WHERE last_seen_at < now() - interval '1 day'")
            )

    def _rate_slots(self) -> int:
        now = time.monotonic()
        self._sent_times = [t for t in self._sent_times if now - t < 60]
        return max(self.settings.email_max_per_minute - len(self._sent_times), 0)

    async def email_tick(self) -> None:
        if self.stopping.is_set() or time.time() < self.email_paused_until:
            return
        capacity = min(self.settings.email_concurrency - len(self.in_flight), self._rate_slots())
        if capacity <= 0:
            return
        async with sessionmaker()() as db:
            ids = await lease_jobs(db, self.worker_id, capacity)
        for job_id in ids:
            self.leased_not_started.add(job_id)
            self._sent_times.append(time.monotonic())
            task = asyncio.create_task(self._run_job(job_id))
            self.in_flight.add(task)
            task.add_done_callback(self.in_flight.discard)

    async def _run_job(self, job_id: uuid.UUID) -> None:
        self.leased_not_started.discard(job_id)
        result = await process_job(sessionmaker(), job_id, self.worker_id, self.provider, self.settings)
        if result is not None and result.config_error:
            # Stop hammering a provider that rejects our configuration; queued jobs wait.
            self.email_paused_until = time.time() + CONFIG_PAUSE_SECONDS
            log.warning(
                "email paused after configuration error",
                extra={"error_code": result.error_code, "pause_seconds": CONFIG_PAUSE_SECONDS},
            )

    # ---- lifecycle ------------------------------------------------------------

    async def run(self) -> None:
        log.info("worker starting", extra={"worker_id": self.worker_id, "provider": self.provider.name})
        loops = [
            asyncio.create_task(self._every(1.0, self.schedule_tick, "schedule")),
            asyncio.create_task(self._every(1.0, self.email_tick, "email")),
            asyncio.create_task(self._every(10.0, self.maintenance_tick, "maintenance")),
            asyncio.create_task(self._every(10.0, self.heartbeat, "heartbeat")),
        ]
        await self.stopping.wait()
        log.info("worker stopping", extra={"in_flight": len(self.in_flight)})
        for task in loops:
            task.cancel()
        for task in loops:
            with contextlib.suppress(asyncio.CancelledError):
                await task
        if self.in_flight:
            await asyncio.wait(self.in_flight, timeout=20)
        async with sessionmaker()() as db:
            await release_unstarted(db, self.worker_id, list(self.leased_not_started))
        log.info("worker stopped")

    def request_stop(self) -> None:
        self.stopping.set()


async def run_worker() -> None:
    settings = get_settings()
    settings.validate_for_runtime()
    configure_logging(settings.log_level)
    init_engine(settings)
    worker = Worker(settings)
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, worker.request_stop)
    try:
        await worker.run()
    finally:
        await dispose_engine()
