"""WebSocket endpoints. Authentication uses the session cookie and a validated Origin;
no token ever travels in the query string."""

from __future__ import annotations

import asyncio
import contextlib
import json
import time

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from ..config import get_settings
from ..db import sessionmaker
from ..deps import load_admin_session, load_participant_session, origin_allowed
from ..errors import AppError
from ..logging import get_logger
from ..models import Programme
from .hub import Connection, get_hub

router = APIRouter()
log = get_logger(__name__)

IDLE_PING_SECONDS = 20
REVALIDATE_SECONDS = 60


def _ip(ws: WebSocket) -> str:
    return ws.client.host if ws.client else "unknown"


async def _authenticate(ws: WebSocket, role: str) -> Connection | None:
    settings = get_settings()
    if not origin_allowed(ws.headers.get("origin")):
        await ws.close(code=4403)
        return None
    try:
        async with sessionmaker()() as db:
            if role == "participant":
                p = await load_participant_session(
                    db, ws.cookies.get(settings.participant_cookie_name), _ip(ws)
                )
                return Connection(ws, role, p.programme_id, p.session_id, p.participant_id)
            a = await load_admin_session(db, ws.cookies.get(settings.admin_cookie_name), _ip(ws))
            return Connection(ws, role, None, a.session_id, a.admin_id)
    except AppError as exc:
        await ws.close(code=4401, reason=exc.code)
        return None


async def _still_valid(conn: Connection) -> bool:
    settings = get_settings()
    try:
        async with sessionmaker()() as db:
            if conn.role == "participant":
                await load_participant_session(
                    db, conn.ws.cookies.get(settings.participant_cookie_name), _ip(conn.ws)
                )
            else:
                await load_admin_session(db, conn.ws.cookies.get(settings.admin_cookie_name), _ip(conn.ws))
        return True
    except AppError:
        return False


async def _hello(conn: Connection) -> dict[str, object]:
    async with sessionmaker()() as db:
        if conn.programme_id is not None:
            programme = await db.get(Programme, conn.programme_id)
        else:
            from sqlalchemy import select

            programme = (
                await db.execute(select(Programme).where(Programme.is_active.is_(True)))
            ).scalar_one_or_none()
            if programme is not None:
                conn.programme_id = programme.id
    return {
        "type": "hello",
        "role": conn.role,
        "v": programme.state_version if programme else None,
        "lv": programme.live_version if programme else None,
        "t": time.time(),
    }


async def _serve(ws: WebSocket, role: str) -> None:
    hub = get_hub()
    conn = await _authenticate(ws, role)
    if conn is None:
        return
    if hub is None:
        await ws.close(code=1013)
        return
    await ws.accept()
    writer = asyncio.create_task(conn.writer())
    hub.register(conn)
    conn.push(await _hello(conn))
    last_check = time.monotonic()
    try:
        while conn.close_code is None:
            try:
                raw = await asyncio.wait_for(ws.receive_text(), timeout=IDLE_PING_SECONDS)
            except TimeoutError:
                conn.push({"type": "ping", "t": time.time()})
                raw = None
            if raw:
                with contextlib.suppress(ValueError):
                    message = json.loads(raw)
                    if isinstance(message, dict) and message.get("type") == "ping":
                        conn.push({"type": "pong", "t": time.time(), "echo": message.get("t")})
            if time.monotonic() - last_check > REVALIDATE_SECONDS:
                last_check = time.monotonic()
                if not await _still_valid(conn):
                    conn.push({"type": "revoked", "reason": "session"})
                    conn.close(4401)
    except WebSocketDisconnect:
        pass
    except Exception as exc:
        log.info("websocket ended", extra={"error": type(exc).__name__})
    finally:
        hub.unregister(conn)
        conn.close(conn.close_code or 1000)
        with contextlib.suppress(Exception):
            await asyncio.wait_for(writer, timeout=5)


@router.websocket("/ws")
async def participant_socket(ws: WebSocket) -> None:
    await _serve(ws, "participant")


@router.websocket("/ws/admin")
async def admin_socket(ws: WebSocket) -> None:
    await _serve(ws, "admin")
