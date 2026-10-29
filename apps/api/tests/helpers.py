"""Test helpers.

* `Factory` creates rows directly with the database *owner* role. It is used only for
  setup and inspection; every behaviour under test goes through the HTTP API or the
  service/worker functions that production code uses.
* `ParticipantClient` / `AdminClient` wrap an `httpx.AsyncClient` (one cookie jar per
  browser) talking to the ASGI app in-process. A participant browser keeps its session and
  device cookies, so the browser that first entered with a sign-in link can come back.
* Every clock comparison uses database time (`clock_timestamp()`), never the test
  process clock.
"""

from __future__ import annotations

import asyncio
import itertools
import json
import os
import uuid
from collections.abc import Awaitable, Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import asyncpg
import httpx
from fastapi import FastAPI
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from stagevote.db import asyncpg_dsn
from stagevote.models import AdminUser, Ballot, Event, Nominee, Participant, Programme
from stagevote.security import decrypt_payload, hash_password_sync, new_token, new_user_code, token_hash
from stagevote.services.invitations import LINK_TOKEN_KEY
from stagevote.worker.runner import Worker

API_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = API_DIR.parents[1]
ORIGIN = "http://testserver"
API = "/api/v1"
ADMIN_API = "/api/v1/admin"
# Plain names: the tests run over http, where the __Host- prefixed names cannot be used.
PARTICIPANT_COOKIE = "sv_session"
DEVICE_COOKIE = "sv_device"
ADMIN_COOKIE = "sv_admin"
TEST_DB_PREFIX = "stagevote_test_"
APP_ROLE = "stagevote_app"
OWNER_ROLE = "stagevote_owner"

ADMIN_PASSWORD = "Organiser-Strong-Passphrase-7"
# Hashed once: Argon2 is deliberately slow and most tests only need *a* valid hash.
_ADMIN_HASH = hash_password_sync(ADMIN_PASSWORD)

_client_ips = itertools.count(1)


@dataclass(frozen=True)
class DatabaseInfo:
    name: str
    owner_url: str  # owner role, test database (migrations, setup)
    app_url: str  # runtime role, test database (the app under test)
    super_url: str  # superuser, test database (fault injection only)
    super_admin_url: str  # superuser, maintenance database (CREATE/DROP DATABASE only)


def mailpit_api() -> str:
    return f"http://127.0.0.1:{os.environ.get('MAILPIT_UI_PORT') or '8025'}/api/v1"


# ---- throwaway databases ------------------------------------------------------------


def database_named(base: DatabaseInfo, name: str) -> DatabaseInfo:
    """Another database on the same server, reached with the same roles."""
    assert name.startswith(TEST_DB_PREFIX)

    def rename(url: str) -> str:
        return make_url(url).set(database=name).render_as_string(hide_password=False)

    return DatabaseInfo(
        name=name,
        owner_url=rename(base.owner_url),
        app_url=rename(base.app_url),
        super_url=rename(base.super_url),
        super_admin_url=base.super_admin_url,
    )


def in_thread[T](fn: Callable[..., T], *args: Any) -> T:
    """Run blocking/asyncio.run code outside pytest-asyncio's event loop."""
    with ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(fn, *args).result()


async def create_database(db: DatabaseInfo) -> None:
    conn = await asyncpg.connect(asyncpg_dsn(db.super_admin_url))
    try:
        await conn.execute(f'CREATE DATABASE "{db.name}" OWNER {OWNER_ROLE}')
    finally:
        await conn.close()
    conn = await asyncpg.connect(asyncpg_dsn(db.super_url))
    try:
        # Same schema preparation as infra/postgres/init/10-stagevote-roles.sh.
        await conn.execute(
            f"ALTER SCHEMA public OWNER TO {OWNER_ROLE}; "
            "REVOKE ALL ON SCHEMA public FROM PUBLIC; "
            f"GRANT USAGE ON SCHEMA public TO {APP_ROLE};"
        )
    finally:
        await conn.close()


async def drop_database(db: DatabaseInfo) -> None:
    assert db.name.startswith(TEST_DB_PREFIX) and db.name != "stagevote"
    conn = await asyncpg.connect(asyncpg_dsn(db.super_admin_url))
    try:
        await conn.execute(f'DROP DATABASE IF EXISTS "{db.name}" WITH (FORCE)')
    finally:
        await conn.close()


def migrate(owner_url: str, revision: str = "head", *, downgrade: bool = False) -> None:
    """Alembic as the owner role, exactly as deployments run it (blocking: use in_thread)."""
    from alembic import command
    from alembic.config import Config

    cfg = Config(str(API_DIR / "alembic.ini"))
    cfg.attributes["url"] = owner_url
    cfg.attributes["app_role"] = APP_ROLE
    cfg.attributes["skip_logging_config"] = True  # keep pytest's logging intact
    if downgrade:
        command.downgrade(cfg, revision)
    else:
        command.upgrade(cfg, revision)


# ---- data ---------------------------------------------------------------------


@dataclass
class BallotInfo:
    id: uuid.UUID
    nominee_ids: list[uuid.UUID]


@dataclass
class EventInfo:
    id: uuid.UUID
    name: str
    ballots: list[BallotInfo]

    @property
    def ballot(self) -> BallotInfo:
        return self.ballots[0]


@dataclass
class ParticipantInfo:
    id: uuid.UUID
    user_code: str
    email: str
    link_token: str | None  # the token of the participant's sign-in link (None: never invited)


@dataclass
class AdminInfo:
    id: uuid.UUID
    email: str
    password: str


class Factory:
    """Direct row creation and inspection with the owner role (test setup only)."""

    def __init__(self, maker: async_sessionmaker[AsyncSession]) -> None:
        self.maker = maker

    # ---- inspection ---------------------------------------------------------

    async def now(self) -> datetime:
        async with self.maker() as db:
            value: datetime = (await db.execute(text("SELECT clock_timestamp()"))).scalar_one()
            return value

    async def fetch(self, sql: str, **params: Any) -> list[Any]:
        async with self.maker() as db:
            return list((await db.execute(text(sql), params)).all())

    async def fetchrow(self, sql: str, **params: Any) -> Any:
        rows = await self.fetch(sql, **params)
        assert len(rows) <= 1, f"expected at most one row, got {len(rows)}"
        return rows[0] if rows else None

    async def fetchval(self, sql: str, **params: Any) -> Any:
        async with self.maker() as db:
            return (await db.execute(text(sql), params)).scalar()

    async def execute(self, sql: str, **params: Any) -> list[Any]:
        """Run one statement in its own committed transaction; returns RETURNING rows."""
        async with self.maker() as db, db.begin():
            result = await db.execute(text(sql), params)
            return list(result.all()) if result.returns_rows else []

    async def row_json(self, table: str, row_id: Any) -> str:
        """The whole row rendered as JSON text (for "secret never stored" checks)."""
        assert table.isidentifier()
        value = await self.fetchval(f"SELECT row_to_json(t)::text FROM {table} t WHERE id = :id", id=row_id)
        assert value is not None, f"{table} row {row_id} not found"
        return str(value)

    async def programme_text(self, programme_id: uuid.UUID) -> str:
        """Audit log, outbox and email attempts of a programme as one JSON blob."""
        rows = await self.fetch(
            """
            SELECT row_to_json(a)::text AS j FROM audit_log a WHERE a.programme_id = :p
            UNION ALL SELECT row_to_json(o)::text FROM outbox_events o WHERE o.programme_id = :p
            UNION ALL SELECT row_to_json(t)::text FROM email_attempts t
                JOIN email_jobs j ON j.id = t.job_id WHERE j.programme_id = :p
            """,
            p=programme_id,
        )
        return "\n".join(r.j for r in rows)

    # ---- creation -------------------------------------------------------------

    async def create_programme(
        self,
        *,
        activate: bool = True,
        run_mode: str = "manual",
        opens_in: timedelta | None = timedelta(hours=-1),
        closes_in: timedelta | None = timedelta(hours=6),
        **fields: Any,
    ) -> uuid.UUID:
        async with self.maker() as db, db.begin():
            now = (await db.execute(text("SELECT clock_timestamp()"))).scalar_one()
            if activate:
                await db.execute(text("UPDATE programmes SET is_active = false WHERE is_active"))
            programme = Programme(
                id=uuid.uuid4(),
                slug=f"test-{uuid.uuid4().hex[:16]}",
                is_active=activate,
                name=fields.pop("name", "Test Showcase"),
                timezone="Asia/Kolkata",
                run_mode=run_mode,
                login_opens_at=now + opens_in if opens_in is not None else None,
                login_closes_at=now + closes_in if closes_in is not None else None,
                **fields,
            )
            db.add(programme)
        return programme.id

    async def create_event(
        self,
        programme_id: uuid.UUID,
        *,
        name: str | None = None,
        ballots: int = 1,
        nominees: int = 3,
        kinds: tuple[str, ...] | None = None,
        planned_start: datetime | None = None,
        planned_end: datetime | None = None,
    ) -> EventInfo:
        """`kinds` gives one ballot per kind (instead of `ballots` team ballots); a text
        ballot never gets nominees."""
        ballot_kinds = kinds if kinds is not None else ("team",) * ballots
        async with self.maker() as db, db.begin():
            position = (
                await db.execute(
                    text("SELECT coalesce(max(position), 0) + 1 FROM events WHERE programme_id = :p"),
                    {"p": programme_id},
                )
            ).scalar_one()
            event = Event(
                id=uuid.uuid4(),
                programme_id=programme_id,
                position=position,
                name=name or f"Round {position}",
                planned_start_at=planned_start,
                planned_end_at=planned_end,
            )
            db.add(event)
            await db.flush()
            infos: list[BallotInfo] = []
            for b, kind in enumerate(ballot_kinds):
                ballot = Ballot(
                    id=uuid.uuid4(),
                    programme_id=programme_id,
                    event_id=event.id,
                    position=b + 1,
                    title=f"{event.name} ballot {b + 1}",
                    kind=kind,
                )
                db.add(ballot)
                await db.flush()
                nominee_ids = []
                for n in range(0 if kind == "text" else nominees):
                    nominee = Nominee(
                        id=uuid.uuid4(), ballot_id=ballot.id, position=n + 1, name=f"Act {b + 1}.{n + 1}"
                    )
                    db.add(nominee)
                    nominee_ids.append(nominee.id)
                infos.append(BallotInfo(ballot.id, nominee_ids))
            await db.flush()
            # Participant snapshots cache the structure per state_version.
            await db.execute(
                text("UPDATE programmes SET state_version = state_version + 1 WHERE id = :p"),
                {"p": programme_id},
            )
        return EventInfo(event.id, event.name, infos)

    async def create_participant(
        self,
        programme_id: uuid.UUID,
        *,
        activated: bool = True,
        email: str | None = None,
        display_name: str = "Test Participant",
    ) -> ParticipantInfo:
        """An activated participant counts as signed in before and holds an unbound sign-in
        link (only the token's hash is stored): the first browser that enters with it keeps
        it. `activated=False` is someone imported and never invited: no link."""
        email = email or f"p-{uuid.uuid4().hex[:12]}@example.com"
        assert email.endswith("@example.com"), "tests only ever use example.com recipients"
        link_token = new_token() if activated else None
        async with self.maker() as db, db.begin():
            now = (await db.execute(text("SELECT clock_timestamp()"))).scalar_one()
            participant = Participant(
                id=uuid.uuid4(),
                programme_id=programme_id,
                email=email,
                email_normalized=email.lower(),
                display_name=display_name,
                user_code=new_user_code(),
                link_token_hash=token_hash(link_token) if link_token else None,
                link_issued_at=now if activated else None,
                activated_at=now if activated else None,
            )
            db.add(participant)
        return ParticipantInfo(participant.id, participant.user_code, email, link_token)

    async def create_admin(self) -> AdminInfo:
        email = f"admin-{uuid.uuid4().hex[:12]}@example.com"
        async with self.maker() as db, db.begin():
            admin = AdminUser(
                id=uuid.uuid4(), email=email, display_name="Test Organiser", password_hash=_ADMIN_HASH
            )
            db.add(admin)
        return AdminInfo(admin.id, email, ADMIN_PASSWORD)

    async def touch(self, programme_id: uuid.UUID) -> None:
        """Bump state_version after editing participant-visible rows directly."""
        await self.execute(
            "UPDATE programmes SET state_version = state_version + 1 WHERE id = :p", p=programme_id
        )

    async def retire_programme(self, programme_id: uuid.UUID) -> None:
        """Stop a finished test's programme from feeding work to later tests' workers."""
        async with self.maker() as db, db.begin():
            await db.execute(
                text(
                    "UPDATE schedule_jobs SET status = 'stale', result_detail = 'test teardown' "
                    "WHERE programme_id = :p AND status = 'pending'"
                ),
                {"p": programme_id},
            )
            await db.execute(
                text(
                    "UPDATE email_jobs SET status = 'cancelled', lease_owner = NULL, lease_expires_at = NULL, "
                    "finished_at = now() WHERE programme_id = :p AND status IN ('queued', 'processing')"
                ),
                {"p": programme_id},
            )
            await db.execute(
                text(
                    "UPDATE events SET status = 'closed', closed_at = clock_timestamp(), "
                    "close_reason = 'test_teardown' WHERE programme_id = :p AND status = 'open'"
                ),
                {"p": programme_id},
            )


# ---- HTTP clients -------------------------------------------------------------


def make_http(
    app: FastAPI,
    *,
    ip: str,
    origin: str | None = ORIGIN,
    transport: httpx.AsyncBaseTransport | None = None,
) -> httpx.AsyncClient:
    headers = {"Origin": origin} if origin else {}
    return httpx.AsyncClient(
        transport=transport or httpx.ASGITransport(app=app, client=(ip, 51000)),
        base_url=ORIGIN,
        headers=headers,
        timeout=60,
    )


class ParticipantClient:
    """One participant browser: its own cookie jar (session and device cookies) and CSRF token."""

    def __init__(self, http: httpx.AsyncClient, info: ParticipantInfo) -> None:
        self.http = http
        self.info = info
        self.csrf: str | None = None

    @property
    def token(self) -> str | None:
        return self.http.cookies.get(PARTICIPANT_COOKIE)

    @property
    def device(self) -> str | None:
        return self.http.cookies.get(DEVICE_COOKIE)

    def _link_token(self, token: str | None) -> str:
        value = self.info.link_token if token is None else token
        assert value is not None, "this participant has no sign-in link"
        return value

    async def check(self, token: str | None = None) -> httpx.Response:
        """What the sign-in page asks when the link is opened (before any tap)."""
        return await self.http.post(f"{API}/auth/link/check", json={"token": self._link_token(token)})

    async def enter(self, token: str | None = None) -> httpx.Response:
        """Tap "Enter the arena" with the participant's link (or another link token)."""
        resp = await self.http.post(f"{API}/auth/link", json={"token": self._link_token(token)})
        if resp.status_code == 200:
            await self.refresh()
        return resp

    async def refresh(self) -> dict[str, Any]:
        resp = await self.snapshot()
        assert resp.status_code == 200, resp.text
        data: dict[str, Any] = resp.json()
        self.csrf = data["csrf_token"]
        return data

    async def snapshot(self) -> httpx.Response:
        return await self.http.get(f"{API}/me/snapshot")

    async def event(self, event_id: uuid.UUID) -> httpx.Response:
        return await self.http.get(f"{API}/me/events/{event_id}")

    async def receipt(self, ballot_id: uuid.UUID) -> httpx.Response:
        return await self.http.get(f"{API}/me/ballots/{ballot_id}/receipt")

    async def logout(self) -> httpx.Response:
        return await self.http.post(f"{API}/auth/logout")

    async def vote(
        self,
        ballot_id: uuid.UUID,
        nominee_id: uuid.UUID,
        *,
        key: str | None = None,
        csrf: str | None = None,
        send_key: bool = True,
        send_csrf: bool = True,
    ) -> httpx.Response:
        return await self.submit(
            ballot_id,
            {"nominee_id": str(nominee_id)},
            key=key,
            csrf=csrf,
            send_key=send_key,
            send_csrf=send_csrf,
        )

    async def answer(self, ballot_id: uuid.UUID, answer: str, *, key: str | None = None) -> httpx.Response:
        """Submit a typed answer to a text ballot."""
        return await self.submit(ballot_id, {"answer": answer}, key=key)

    async def submit(
        self,
        ballot_id: uuid.UUID,
        body: dict[str, Any],
        *,
        key: str | None = None,
        csrf: str | None = None,
        send_key: bool = True,
        send_csrf: bool = True,
    ) -> httpx.Response:
        """POST any vote body (a nominee, a typed answer, or something malformed)."""
        headers: dict[str, str] = {}
        if send_key:
            headers["Idempotency-Key"] = key if key is not None else uuid.uuid4().hex
        if send_csrf:
            headers["X-CSRF-Token"] = csrf if csrf is not None else (self.csrf or "")
        return await self.http.post(f"{API}/me/ballots/{ballot_id}/vote", json=body, headers=headers)


class AdminClient:
    def __init__(self, http: httpx.AsyncClient, info: AdminInfo) -> None:
        self.http = http
        self.info = info
        self.csrf = ""

    async def login(self) -> None:
        resp = await self.http.post(
            f"{API}/admin/auth/login", json={"email": self.info.email, "password": self.info.password}
        )
        assert resp.status_code == 200, resp.text
        me = await self.http.get(f"{ADMIN_API}/me")
        assert me.status_code == 200, me.text
        self.csrf = me.json()["csrf_token"]

    async def get(self, path: str, **params: Any) -> httpx.Response:
        return await self.http.get(ADMIN_API + path, params=params or None)

    async def post(self, path: str, body: dict[str, Any] | None = None) -> httpx.Response:
        return await self.http.post(ADMIN_API + path, json=body or {}, headers={"X-CSRF-Token": self.csrf})

    async def patch(self, path: str, body: dict[str, Any]) -> httpx.Response:
        return await self.http.patch(ADMIN_API + path, json=body, headers={"X-CSRF-Token": self.csrf})

    async def put(self, path: str, body: dict[str, Any]) -> httpx.Response:
        return await self.http.put(ADMIN_API + path, json=body, headers={"X-CSRF-Token": self.csrf})

    async def delete(self, path: str) -> httpx.Response:
        return await self.http.delete(ADMIN_API + path, headers={"X-CSRF-Token": self.csrf})

    async def live_version(self) -> int:
        resp = await self.get("/live")
        assert resp.status_code == 200, resp.text
        return int(resp.json()["live_version"])

    async def open_event(
        self, event_id: uuid.UUID, deadline: datetime | None = None, *, expected: int | None = None
    ) -> httpx.Response:
        body: dict[str, Any] = {
            "event_id": str(event_id),
            "expected_live_version": expected if expected is not None else await self.live_version(),
        }
        if deadline is not None:
            body["deadline_at"] = deadline.isoformat()
        return await self.post("/live/open", body)

    async def start_next(
        self, event_id: uuid.UUID, deadline: datetime | None = None, *, expected: int | None = None
    ) -> httpx.Response:
        body: dict[str, Any] = {
            "event_id": str(event_id),
            "expected_live_version": expected if expected is not None else await self.live_version(),
        }
        if deadline is not None:
            body["deadline_at"] = deadline.isoformat()
        return await self.post("/live/start-next", body)

    async def close_event(self, event_id: uuid.UUID, *, expected: int | None = None) -> httpx.Response:
        return await self.post(
            "/live/close",
            {
                "event_id": str(event_id),
                "expected_live_version": expected if expected is not None else await self.live_version(),
            },
        )

    async def settings_version(self) -> int:
        resp = await self.get("/programme")
        assert resp.status_code == 200, resp.text
        return int(resp.json()["settings_version"])


class World:
    """Everything one test needs: a fresh active programme, clients and the factory."""

    def __init__(self, app: FastAPI, factory: Factory, programme_id: uuid.UUID) -> None:
        self.app = app
        self.factory = factory
        self.programme_id = programme_id
        n = next(_client_ips)
        # A distinct client address per test keeps the per-IP rate limits independent.
        self.ip = f"10.{(n >> 16) & 255}.{(n >> 8) & 255}.{n & 255}"
        self._clients: list[httpx.AsyncClient] = []
        self._admin: AdminClient | None = None

    def http(
        self, *, origin: str | None = ORIGIN, transport: httpx.AsyncBaseTransport | None = None
    ) -> httpx.AsyncClient:
        client = make_http(self.app, ip=self.ip, origin=origin, transport=transport)
        self._clients.append(client)
        return client

    async def admin(self, *, fresh: bool = False) -> AdminClient:
        if self._admin is not None and not fresh:
            return self._admin
        client = AdminClient(self.http(), await self.factory.create_admin())
        await client.login()
        if self._admin is None:
            self._admin = client
        return client

    def browser(self, info: ParticipantInfo, **kwargs: Any) -> ParticipantClient:
        """A new browser (empty cookie jar) for an existing participant: another device."""
        return ParticipantClient(self.http(**kwargs), info)

    async def participant(
        self, *, login: bool = True, activated: bool = True, email: str | None = None
    ) -> ParticipantClient:
        """A participant's browser, signed in with their sign-in link unless `login=False`
        (the link is then bound to this browser)."""
        info = await self.factory.create_participant(self.programme_id, activated=activated, email=email)
        client = self.browser(info)
        if login:
            resp = await client.enter()
            assert resp.status_code == 200, resp.text
        return client

    async def event(self, **kwargs: Any) -> EventInfo:
        return await self.factory.create_event(self.programme_id, **kwargs)

    async def live_event(self, *, deadline: datetime | None = None, **kwargs: Any) -> EventInfo:
        """Create an event and open it through the admin live-control API."""
        event = await self.event(**kwargs)
        resp = await (await self.admin()).open_event(event.id, deadline)
        assert resp.status_code == 200, resp.text
        return event

    async def set_window(self, opens_in: timedelta | None, closes_in: timedelta | None) -> None:
        await self.factory.execute(
            "UPDATE programmes SET login_opens_at = clock_timestamp() + :o, "
            "login_closes_at = clock_timestamp() + :c, settings_version = settings_version + 1, "
            "state_version = state_version + 1 WHERE id = :p",
            o=opens_in,
            c=closes_in,
            p=self.programme_id,
        )

    async def aclose(self) -> None:
        for client in self._clients:
            await client.aclose()


# ---- assertions and waiting -----------------------------------------------------


def error_code(resp: httpx.Response) -> str | None:
    try:
        code = resp.json()["error"]["code"]
    except (ValueError, KeyError, TypeError):
        return None
    return str(code)


def assert_error(resp: httpx.Response, status: int, code: str) -> dict[str, Any]:
    assert resp.status_code == status and error_code(resp) == code, (
        f"expected {status} {code}, got {resp.status_code}: {resp.text}"
    )
    error: dict[str, Any] = resp.json()["error"]
    return error


def receipt_of(resp: httpx.Response) -> dict[str, Any]:
    """The receipt carried by a vote response (success body or error details)."""
    body = resp.json()
    receipt = body["receipt"] if "receipt" in body else body["error"]["details"]["receipt"]
    assert isinstance(receipt, dict)
    return receipt


def pg_error(exc: BaseException) -> asyncpg.PostgresError:
    """The asyncpg error at the root of a SQLAlchemy exception chain."""
    current: BaseException | None = exc
    seen: set[int] = set()
    while current is not None and id(current) not in seen:
        if isinstance(current, asyncpg.PostgresError):
            return current
        seen.add(id(current))
        current = current.__cause__ or current.__context__
    raise AssertionError(f"no PostgreSQL error in {exc!r}")


async def eventually[T](
    probe: Callable[[], Awaitable[T | None]], *, within: float = 5.0, interval: float = 0.05, what: str = ""
) -> T:
    loop = asyncio.get_running_loop()
    end = loop.time() + within
    while True:
        value = await probe()
        if value is not None and value is not False:
            return value
        if loop.time() >= end:
            raise AssertionError(f"timed out after {within}s waiting for {what or 'condition'}")
        await asyncio.sleep(interval)


async def wait_until_db_time(factory: Factory, moment: datetime, *, margin: float = 0.05) -> None:
    """Sleep until the *database* clock has passed `moment`."""
    for _ in range(1000):
        remaining = (moment - await factory.now()).total_seconds() + margin
        if remaining <= 0:
            return
        await asyncio.sleep(min(remaining, 0.25))
    raise AssertionError(f"database clock never reached {moment}")


async def advisory_lock_waiters(factory: Factory) -> int:
    """Transactions currently queued on an advisory lock in the test database."""
    return int(
        await factory.fetchval(
            "SELECT count(*) FROM pg_locks l JOIN pg_database d ON d.oid = l.database "
            "WHERE l.locktype = 'advisory' AND NOT l.granted AND d.datname = current_database()"
        )
    )


async def drain(worker: Worker) -> None:
    """Wait for every email task the worker started."""
    while worker.in_flight:
        await asyncio.wait(set(worker.in_flight))


def secret_of(ciphertext: bytes | None, field: str = LINK_TOKEN_KEY) -> str:
    """The plaintext an email job carries (by default the sign-in link token)."""
    assert ciphertext is not None, "credential was already purged"
    value = decrypt_payload(bytes(ciphertext))[field]
    assert isinstance(value, str)
    return value


def dumps(value: Any) -> str:
    return json.dumps(value, default=str)
