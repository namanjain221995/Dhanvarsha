"""Two API processes: live updates cross process boundaries through PostgreSQL.

Starts two real `uvicorn stagevote.main:app` processes on random ports against the test
database (runtime role). Participant WebSockets connect to one process while the admin
acts through the other, so nothing can be delivered through in-memory state alone.
"""

from __future__ import annotations

import asyncio
import json
import os
import socket
import sys
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import IO, Any

import asyncpg
import httpx
import pytest
from websockets.asyncio.client import ClientConnection, connect
from websockets.exceptions import ConnectionClosed, InvalidStatus

from helpers import API_DIR, AdminClient, DatabaseInfo, Factory, ParticipantClient, World, eventually
from stagevote.db import asyncpg_dsn


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _open_log(path: Path) -> IO[bytes]:
    return path.open("wb")


@dataclass
class ApiProcess:
    port: int
    origin: str
    process: asyncio.subprocess.Process
    log_path: Path
    log: IO[bytes]

    def output(self) -> str:
        self.log.flush()
        return self.log_path.read_text(errors="replace")[-4000:]


@pytest.fixture
async def api_processes(
    database: DatabaseInfo, world: World, tmp_path: Path
) -> AsyncIterator[list[ApiProcess]]:
    ports = [_free_port(), _free_port()]
    origins = [f"http://127.0.0.1:{port}" for port in ports]
    env = os.environ | {
        "DATABASE_URL": database.app_url,  # runtime role, test database
        "APP_BASE_URL": origins[0],
        "EXTRA_ALLOWED_ORIGINS": ",".join(origins),
        "EMAIL_PROVIDER": "disabled",
        "LOG_LEVEL": "WARNING",
        "DB_POOL_SIZE": "4",
        "DB_MAX_OVERFLOW": "4",
    }
    processes: list[ApiProcess] = []
    try:
        for port, origin in zip(ports, origins, strict=True):
            log_path = tmp_path / f"api-{port}.log"
            log = _open_log(log_path)
            process = await asyncio.create_subprocess_exec(
                sys.executable,
                "-m",
                "uvicorn",
                "stagevote.main:app",
                "--host=127.0.0.1",
                f"--port={port}",
                "--no-access-log",
                "--log-level=warning",
                "--timeout-graceful-shutdown=5",
                cwd=API_DIR,
                env=env,
                stdout=log,
                stderr=asyncio.subprocess.STDOUT,
            )
            processes.append(ApiProcess(port, origin, process, log_path, log))

        async with httpx.AsyncClient(timeout=2, trust_env=False) as probe:
            for proc in processes:

                async def ready(proc: ApiProcess = proc) -> bool:
                    if proc.process.returncode is not None:
                        raise AssertionError(f"API process exited early:\n{proc.output()}")
                    try:
                        resp = await probe.get(f"{proc.origin}/api/v1/ready")
                    except httpx.TransportError:
                        return False
                    return resp.status_code == 200 and resp.json()["live_listener"] is True

                await eventually(ready, within=30, interval=0.2, what=f"API on port {proc.port}")
        yield processes
    finally:
        for proc in processes:
            if proc.process.returncode is None:
                proc.process.terminate()
        for proc in processes:
            try:
                await asyncio.wait_for(proc.process.wait(), timeout=15)
            except TimeoutError:
                proc.process.kill()
                await proc.process.wait()
            proc.log.close()


async def open_socket(proc: ApiProcess, token: str | None) -> ClientConnection:
    ws = await connect(
        f"ws://127.0.0.1:{proc.port}/ws",
        origin=proc.origin,  # type: ignore[arg-type]
        additional_headers={"Cookie": f"sv_session={token}"},
        proxy=None,
        open_timeout=10,
    )
    hello = json.loads(await asyncio.wait_for(ws.recv(), timeout=5))
    assert (hello["type"], hello["role"]) == ("hello", "participant")
    return ws


async def collect(
    ws: ClientConnection, done: Callable[[list[dict[str, Any]]], bool], within: float, what: str
) -> list[dict[str, Any]]:
    """Read messages until `done(messages)` holds; fail after `within` seconds."""
    loop = asyncio.get_running_loop()
    end = loop.time() + within
    messages: list[dict[str, Any]] = []
    while not done(messages):
        remaining = end - loop.time()
        if remaining <= 0:
            raise AssertionError(f"timed out after {within}s waiting for {what}; got {messages}")
        try:
            messages.append(json.loads(await asyncio.wait_for(ws.recv(), timeout=remaining)))
        except TimeoutError:
            continue
    return messages


def has(kind: str, **fields: Any) -> Callable[[list[dict[str, Any]]], bool]:
    return lambda ms: any(m.get("type") == kind and all(m.get(k) == v for k, v in fields.items()) for m in ms)


async def test_live_updates_and_revocation_cross_processes_and_survive_listener_loss(
    world: World, factory: Factory, database: DatabaseInfo, api_processes: list[ApiProcess]
) -> None:
    proc_a, proc_b = api_processes
    first = await world.event()
    second = await world.event()
    voter_info = await factory.create_participant(world.programme_id)
    sockets: list[ClientConnection] = []
    async with (
        httpx.AsyncClient(
            base_url=proc_b.origin, headers={"Origin": proc_b.origin}, trust_env=False, timeout=15
        ) as on_b,
        httpx.AsyncClient(
            base_url=proc_a.origin, headers={"Origin": proc_a.origin}, trust_env=False, timeout=15
        ) as on_a,
    ):
        admin = AdminClient(on_b, await factory.create_admin())
        await admin.login()
        voter = ParticipantClient(on_a, voter_info)
        assert (await voter.login()).status_code == 200
        try:
            # WebSocket authentication: the Origin and the session cookie are both required.
            for origin, cookie in (("https://evil.example", voter.token), (proc_a.origin, "forged-token")):
                with pytest.raises(InvalidStatus) as rejected:
                    await connect(
                        f"ws://127.0.0.1:{proc_a.port}/ws",
                        origin=origin,  # type: ignore[arg-type]
                        additional_headers={"Cookie": f"sv_session={cookie}"},
                        proxy=None,
                    )
                assert rejected.value.response.status_code == 403

            # 1. A transition committed through process B reaches a socket on process A.
            ws_a = await open_socket(proc_a, voter.token)
            sockets.append(ws_a)
            version = await admin.live_version()
            assert (await admin.open_event(first.id, expected=version)).status_code == 200
            await collect(ws_a, has("state", lv=version + 1), within=2, what="the state signal on process A")

            # 2. Revoking the session through B closes the socket on A with 4401.
            assert (await admin.post(f"/participants/{voter_info.id}/reset-session")).status_code == 200
            await collect(ws_a, has("revoked", reason="admin_reset"), within=2, what="the revocation notice")
            with pytest.raises(ConnectionClosed) as closed:
                await asyncio.wait_for(ws_a.recv(), timeout=5)
            assert closed.value.rcvd is not None and closed.value.rcvd.code == 4401

            # 3. Listener connections die: a change made meanwhile still arrives (outbox
            #    catch-up) and every client is told to resync once the listener is back.
            assert (await voter.login()).status_code == 200
            ws_a = await open_socket(proc_a, voter.token)
            ws_b = await open_socket(proc_b, voter.token)
            sockets += [ws_a, ws_b]
            superuser = await asyncpg.connect(asyncpg_dsn(database.super_url))
            try:
                killed = await superuser.fetchval(
                    "SELECT count(*) FILTER (WHERE pg_terminate_backend(pid)) FROM pg_stat_activity "
                    "WHERE application_name = 'stagevote-listener' AND datname = $1",
                    database.name,
                )
                assert killed == 2
                version = await admin.live_version()
                assert (await admin.start_next(second.id, expected=version)).status_code == 200
                for ws in (ws_a, ws_b):
                    await collect(
                        ws,
                        lambda ms, v=version: (
                            has("state", lv=v + 1)(ms) and has("resync", reason="listener_reconnected")(ms)
                        ),
                        within=20,
                        what="the missed state signal and a resync",
                    )

                # 4. A notification that is never sent (trigger bypassed in replica mode)
                #    is still delivered by the outbox catch-up poll.
                await superuser.execute("SET session_replication_role = replica")
                await superuser.execute(
                    "INSERT INTO outbox_events (programme_id, topic, payload) VALUES ($1, 'state', $2::jsonb)",
                    world.programme_id,
                    json.dumps({"v": 424242, "lv": 4242, "r": "test"}),
                )
                await superuser.execute("RESET session_replication_role")
                for ws in (ws_a, ws_b):
                    await collect(ws, has("state", v=424242, lv=4242), within=6, what="the catch-up delivery")
            finally:
                await superuser.close()
        finally:
            for ws in sockets:
                await ws.close()
