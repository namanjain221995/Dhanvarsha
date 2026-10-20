"""Per-process WebSocket hub fed by PostgreSQL notifications.

Every API process runs one hub. It LISTENs on the `sv_outbox` channel through a
dedicated connection, so a change committed through any process reaches clients
connected to every process. Messages are small signals ("state changed to version N");
clients always refetch an authorised snapshot over HTTP.

Recovery paths:
* The outbox table is polled every few seconds for rows the listener did not see
  (lost notification, listener reconnecting).
* After the listener reconnects, every local client is told to resync.
* Clients reconnect with backoff and refetch on reconnect, on visibility change and on a
  slow polling fallback, so a total notification outage only delays updates.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import random
import time
import uuid
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Any

import asyncpg
from sqlalchemy import text
from starlette.websockets import WebSocket, WebSocketState

from ..db import asyncpg_dsn, sessionmaker
from ..logging import get_logger

log = get_logger(__name__)
CHANNEL = "sv_outbox"
SEND_QUEUE_LIMIT = 64


@dataclass(eq=False)
class Connection:
    ws: WebSocket
    role: str  # participant | admin
    programme_id: uuid.UUID | None
    session_id: uuid.UUID
    principal_id: uuid.UUID
    queue: asyncio.Queue[str | None] = field(default_factory=lambda: asyncio.Queue(SEND_QUEUE_LIMIT))
    close_code: int | None = None

    def push(self, message: dict[str, Any]) -> None:
        try:
            self.queue.put_nowait(json.dumps(message, default=str))
        except asyncio.QueueFull:
            # A client too slow to drain its queue is disconnected; it will reconnect and
            # fetch a fresh snapshot.
            self.close(4408)

    def close(self, code: int) -> None:
        if self.close_code is None:
            self.close_code = code
            with contextlib.suppress(asyncio.QueueFull):
                self.queue.put_nowait(None)

    async def writer(self) -> None:
        try:
            while True:
                item = await self.queue.get()
                if item is None:
                    break
                await asyncio.wait_for(self.ws.send_text(item), timeout=10)
        except Exception:
            self.close_code = self.close_code or 1011
        finally:
            if self.ws.application_state == WebSocketState.CONNECTED:
                with contextlib.suppress(Exception):
                    await self.ws.close(code=self.close_code or 1000)


class LiveHub:
    def __init__(self, database_url: str) -> None:
        self._dsn = asyncpg_dsn(database_url)
        self._connections: set[Connection] = set()
        self._seen: OrderedDict[int, float] = OrderedDict()
        self._tally_dirty: dict[uuid.UUID, set[str]] = {}
        self._email_dirty: set[uuid.UUID] = set()
        self._tasks: list[asyncio.Task[None]] = []
        self._running = False
        self.listener_connected = False
        self._listener_generation = 0
        self.stats = {"notifications": 0, "catchup": 0, "resyncs": 0}

    # ---- lifecycle ------------------------------------------------------------

    async def start(self) -> None:
        self._running = True
        self._tasks = [
            asyncio.create_task(self._listen_loop(), name="hub-listen"),
            asyncio.create_task(self._catchup_loop(), name="hub-catchup"),
            asyncio.create_task(self._coalesce_loop(), name="hub-coalesce"),
        ]

    async def stop(self) -> None:
        self._running = False
        for task in self._tasks:
            task.cancel()
        for task in self._tasks:
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task
        for conn in list(self._connections):
            conn.close(1001)

    # ---- registry -------------------------------------------------------------

    def register(self, conn: Connection) -> None:
        self._connections.add(conn)

    def unregister(self, conn: Connection) -> None:
        self._connections.discard(conn)

    @property
    def connection_count(self) -> int:
        return len(self._connections)

    def _targets(self, programme_id: uuid.UUID | None, role: str | None = None) -> list[Connection]:
        return [
            c
            for c in self._connections
            if (programme_id is None or c.programme_id is None or c.programme_id == programme_id)
            and (role is None or c.role == role)
        ]

    # ---- inbound notifications ------------------------------------------------

    def _on_notify(self, _conn: Any, _pid: int, _channel: str, payload: str) -> None:
        try:
            event = json.loads(payload)
        except ValueError:
            return
        self.stats["notifications"] += 1
        self.dispatch(event)

    def dispatch(self, event: dict[str, Any]) -> None:
        event_id = int(event.get("id", 0))
        if event_id:
            if event_id in self._seen:
                return
            self._seen[event_id] = time.monotonic()
            while len(self._seen) > 20000:
                self._seen.popitem(last=False)
        programme_id = uuid.UUID(event["p"]) if event.get("p") else None
        topic = event.get("t")
        data = event.get("d") or {}
        ts = event.get("ts")
        if topic == "state":
            message = {"type": "state", "v": data.get("v"), "lv": data.get("lv"), "ts": ts}
            for conn in self._targets(programme_id):
                conn.push(message)
        elif topic == "tally" and programme_id is not None:
            self._tally_dirty.setdefault(programme_id, set()).add(str(data.get("e")))
        elif topic == "email" and programme_id is not None:
            self._email_dirty.add(programme_id)
        elif topic == "session":
            participant = data.get("pid")
            # Only the named sessions close: a newer valid session must keep its socket.
            sessions = set(data.get("sids") or ([data["sid"]] if data.get("sid") else []))
            for conn in self._targets(programme_id, "participant"):
                if str(conn.principal_id) == participant and (
                    not sessions or str(conn.session_id) in sessions
                ):
                    conn.push({"type": "revoked", "reason": data.get("r", "logout")})
                    conn.close(4401)
        elif topic == "admin_session":
            session = data.get("sid")
            for conn in self._targets(None, "admin"):
                if str(conn.principal_id) == data.get("aid") and (
                    session is None or str(conn.session_id) == session
                ):
                    conn.push({"type": "revoked", "reason": data.get("r", "logout")})
                    conn.close(4401)

    def resync_all(self, reason: str) -> None:
        self.stats["resyncs"] += 1
        for conn in list(self._connections):
            conn.push({"type": "resync", "reason": reason})

    # ---- loops ----------------------------------------------------------------

    async def _listen_loop(self) -> None:
        backoff = 0.5
        while self._running:
            conn: asyncpg.Connection | None = None
            try:
                conn = await asyncpg.connect(
                    self._dsn, server_settings={"application_name": "stagevote-listener"}
                )
                await conn.add_listener(CHANNEL, self._on_notify)
                self.listener_connected = True
                self._listener_generation += 1
                if self._listener_generation > 1:
                    # Notifications may have been missed while disconnected.
                    self.resync_all("listener_reconnected")
                log.info("live listener connected", extra={"generation": self._listener_generation})
                backoff = 0.5
                while self._running and not conn.is_closed():
                    await asyncio.sleep(5)
                    await asyncio.wait_for(conn.fetchval("SELECT 1"), timeout=5)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                log.warning("live listener disconnected", extra={"error": type(exc).__name__})
            finally:
                self.listener_connected = False
                if conn is not None and not conn.is_closed():
                    with contextlib.suppress(Exception):
                        await conn.close(timeout=2)
            if self._running:
                await asyncio.sleep(backoff + random.uniform(0, backoff))
                backoff = min(backoff * 2, 10)

    async def _catchup_loop(self) -> None:
        """Deliver outbox rows whose notification never arrived (at most a few seconds late)."""
        while self._running:
            await asyncio.sleep(3)
            try:
                async with sessionmaker()() as db:
                    rows = (
                        await db.execute(
                            text(
                                "SELECT id, programme_id, topic, payload, extract(epoch FROM created_at) AS ts "
                                "FROM outbox_events WHERE created_at > now() - interval '30 seconds' ORDER BY id"
                            )
                        )
                    ).all()
                for row in rows:
                    if row.id in self._seen:
                        continue
                    self.stats["catchup"] += 1
                    self.dispatch(
                        {
                            "id": row.id,
                            "p": str(row.programme_id) if row.programme_id else None,
                            "t": row.topic,
                            "d": row.payload,
                            "ts": float(row.ts),
                        }
                    )
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                log.warning("outbox catch-up failed", extra={"error": type(exc).__name__})

    async def _coalesce_loop(self) -> None:
        """Admin tally/email refresh signals at most once per second per programme."""
        while self._running:
            await asyncio.sleep(1)
            tally, self._tally_dirty = self._tally_dirty, {}
            for programme_id, events in tally.items():
                for conn in self._targets(programme_id, "admin"):
                    conn.push({"type": "tally", "events": sorted(events)})
            email, self._email_dirty = self._email_dirty, set()
            for programme_id in email:
                for conn in self._targets(programme_id, "admin"):
                    conn.push({"type": "email"})


_hub: LiveHub | None = None


def set_hub(hub: LiveHub | None) -> None:
    global _hub
    _hub = hub


def get_hub() -> LiveHub | None:
    return _hub
