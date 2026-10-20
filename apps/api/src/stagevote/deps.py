"""Request dependencies: origin/CSRF checks and session authentication."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from urllib.parse import urlsplit

from fastapi import Depends, Request
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from .config import get_settings
from .db import get_db
from .errors import AppError, unauthenticated
from .security import csrf_matches, csrf_token_for, token_hash
from .state import Actor

UNSAFE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}


def client_ip(request: Request) -> str:
    # Uvicorn's proxy-headers support has already applied X-Forwarded-For from the
    # trusted proxy, so request.client is the real client address.
    return request.client.host if request.client else "unknown"


def origin_allowed(origin: str | None) -> bool:
    if not origin:
        return False
    parts = urlsplit(origin)
    return f"{parts.scheme}://{parts.netloc}" in get_settings().allowed_origins


def check_origin(request: Request) -> None:
    """Reject cross-site state-changing requests (defence in depth beside SameSite)."""
    if request.method not in UNSAFE_METHODS:
        return
    origin = request.headers.get("origin") or request.headers.get("referer")
    if not origin_allowed(origin):
        raise AppError("BAD_ORIGIN", "This request did not come from the StageVote site.", 403)


@dataclass(frozen=True)
class ParticipantContext:
    session_id: uuid.UUID
    participant_id: uuid.UUID
    programme_id: uuid.UUID
    user_code: str
    display_name: str
    email: str
    expires_at: datetime
    ip: str

    @property
    def csrf_token(self) -> str:
        return csrf_token_for(self.session_id, "participant")

    @property
    def actor(self) -> Actor:
        return Actor("participant", self.participant_id, self.user_code, self.ip)


@dataclass(frozen=True)
class AdminContext:
    session_id: uuid.UUID
    admin_id: uuid.UUID
    email: str
    display_name: str
    ip: str

    @property
    def csrf_token(self) -> str:
        return csrf_token_for(self.session_id, "admin")

    @property
    def actor(self) -> Actor:
        return Actor("admin", self.admin_id, self.email, self.ip)


REVOKE_MESSAGES = {
    "logout": "You signed out. Sign in again to continue.",
    "admin_reset": "Your session was reset by the organisers. Sign in again to continue.",
    "credentials_reissued": "The organisers sent you a new invitation. Sign in with the password from that email.",
    "password_reset": "Your password was changed. Sign in with your new password.",
    "disabled": "This account has been disabled. Contact the organisers for help.",
    "expired": "Your session expired. Sign in again to continue.",
}


async def load_participant_session(db: AsyncSession, token: str | None, ip: str) -> ParticipantContext:
    if not token:
        raise unauthenticated()
    row = (
        await db.execute(
            text(
                """
                SELECT s.id AS session_id, s.participant_id, s.programme_id, s.expires_at, s.revoked_at,
                       s.revoke_reason, s.last_seen_at < now() - interval '5 minutes' AS stale_seen,
                       s.expires_at <= now() AS expired,
                       p.user_code, p.display_name, p.email, p.disabled_at, p.activated_at
                FROM participant_sessions s JOIN participants p ON p.id = s.participant_id
                WHERE s.token_hash = :h
                """
            ),
            {"h": token_hash(token)},
        )
    ).first()
    if row is None:
        raise unauthenticated()
    if row.revoked_at is not None:
        reason = row.revoke_reason or "logout"
        raise AppError(
            "SESSION_REVOKED", REVOKE_MESSAGES.get(reason, REVOKE_MESSAGES["logout"]), 401, {"reason": reason}
        )
    if row.expired:
        raise AppError("SESSION_EXPIRED", REVOKE_MESSAGES["expired"], 401)
    if row.disabled_at is not None:
        raise AppError("ACCOUNT_DISABLED", REVOKE_MESSAGES["disabled"], 403)
    if row.activated_at is None:
        raise unauthenticated()
    if row.stale_seen:
        await db.execute(
            text(
                "UPDATE participant_sessions SET last_seen_at = now() WHERE id = :sid AND revoked_at IS NULL"
            ),
            {"sid": row.session_id},
        )
    await db.commit()
    return ParticipantContext(
        session_id=row.session_id,
        participant_id=row.participant_id,
        programme_id=row.programme_id,
        user_code=row.user_code,
        display_name=row.display_name,
        email=row.email,
        expires_at=row.expires_at,
        ip=ip,
    )


async def current_participant(request: Request, db: AsyncSession = Depends(get_db)) -> ParticipantContext:
    check_origin(request)
    ctx = await load_participant_session(
        db, request.cookies.get(get_settings().participant_cookie_name), client_ip(request)
    )
    if request.method in UNSAFE_METHODS and not csrf_matches(
        ctx.session_id, "participant", request.headers.get("x-csrf-token")
    ):
        raise AppError("CSRF_FAILED", "Your page is out of date. Reload and try again.", 403)
    return ctx


async def load_admin_session(db: AsyncSession, token: str | None, ip: str) -> AdminContext:
    if not token:
        raise unauthenticated("Sign in as an organiser to continue.")
    row = (
        await db.execute(
            text(
                """
                SELECT s.id AS session_id, s.admin_id, s.revoked_at, s.expires_at <= now() AS expired,
                       s.last_seen_at < now() - interval '5 minutes' AS stale_seen,
                       a.email, a.display_name, a.disabled_at
                FROM admin_sessions s JOIN admin_users a ON a.id = s.admin_id
                WHERE s.token_hash = :h
                """
            ),
            {"h": token_hash(token)},
        )
    ).first()
    if row is None or row.revoked_at is not None or row.expired or row.disabled_at is not None:
        raise unauthenticated("Sign in as an organiser to continue.")
    if row.stale_seen:
        await db.execute(
            text("UPDATE admin_sessions SET last_seen_at = now() WHERE id = :sid"), {"sid": row.session_id}
        )
    await db.commit()
    return AdminContext(row.session_id, row.admin_id, row.email, row.display_name, ip)


async def current_admin(request: Request, db: AsyncSession = Depends(get_db)) -> AdminContext:
    check_origin(request)
    ctx = await load_admin_session(
        db, request.cookies.get(get_settings().admin_cookie_name), client_ip(request)
    )
    if request.method in UNSAFE_METHODS and not csrf_matches(
        ctx.session_id, "admin", request.headers.get("x-csrf-token")
    ):
        raise AppError("CSRF_FAILED", "Your page is out of date. Reload and try again.", 403)
    return ctx
