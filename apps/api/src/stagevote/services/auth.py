"""Participant and admin authentication.

Participants sign in with their email address (or user code) and the password from their
latest invitation email. There is no activation step and no self-service reset: an
organiser re-sends the invitation, which replaces the password.

Single active session (invariant 6): login locks the participant row FOR UPDATE, so
concurrent logins for one account run one after another. The second sees the first's
session and is rejected. A partial unique index on non-revoked sessions backs this up.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import timedelta

from sqlalchemy import or_, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import get_settings
from ..errors import AppError
from ..models import AdminSession, AdminUser, Participant, ParticipantSession, Programme
from ..ratelimit import Limit, clear, enforce, hit
from ..security import new_token, token_hash, verify_password
from ..state import Actor, audit, emit
from .participants import normalize_email
from .programmes import active_programme, login_phase, server_now


def invalid_credentials() -> AppError:
    return AppError(
        "INVALID_CREDENTIALS",
        "That user ID or password is not right. Check your invitation email and try again.",
        401,
    )


async def lock_participant(db: AsyncSession, participant_id: uuid.UUID) -> Participant:
    """SELECT ... FOR UPDATE that also refreshes the identity-mapped instance."""
    return (
        await db.execute(
            select(Participant)
            .where(Participant.id == participant_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    ).scalar_one()


@dataclass
class LoginResult:
    token: str | None
    session_id: uuid.UUID
    participant: Participant
    reused: bool


def _limits(identifier: str, ip: str) -> tuple[Limit, Limit]:
    """Per-identifier and per-IP limits.

    The identifier key is the canonical typed identifier, the same whether or not an
    account exists, so a lockout reveals nothing. The per-IP limit only counts failures
    and is generous, because a whole venue may share one public address.
    """
    s = get_settings()
    canonical = identifier.strip().lower()[:200]
    return (
        Limit(f"login:id:{canonical}", s.login_failures_per_account, s.login_failure_window_seconds),
        Limit(f"login_fail:ip:{ip}", s.login_failures_per_ip, s.login_ip_window_seconds),
    )


async def _find_participant(db: AsyncSession, programme: Programme, identifier: str) -> Participant | None:
    ident = identifier.strip()
    if not ident:
        return None
    conditions = [Participant.user_code == ident.upper()]
    if "@" in ident:
        try:
            _, key = normalize_email(ident)
            conditions.append(Participant.email_normalized == key)
        except ValueError:
            pass
    stmt = select(Participant).where(Participant.programme_id == programme.id, or_(*conditions)).limit(1)
    return (await db.execute(stmt)).scalar_one_or_none()


async def _record_failure(db: AsyncSession, limits: tuple[Limit, Limit]) -> None:
    """The identifier counter was already incremented up front; count the IP failure."""
    async with db.begin():
        await hit(db, limits[1])


async def _reserve_attempt(db: AsyncSession, limits: tuple[Limit, Limit], message: str) -> None:
    """Atomically count this attempt against the identifier before checking the password,
    so parallel guesses cannot all slip past the limit. A success clears the counter."""
    identifier_limit, ip_limit = limits
    await enforce(db, ip_limit, message=message)
    count = await hit(db, identifier_limit)
    if count > identifier_limit.limit:
        raise AppError(
            "RATE_LIMITED",
            message,
            429,
            {"retry_after_seconds": identifier_limit.window_seconds},
            headers={"Retry-After": str(identifier_limit.window_seconds)},
        )


async def participant_login(
    db: AsyncSession, identifier: str, password: str, *, presented_token: str | None, ip: str, user_agent: str
) -> LoginResult:
    settings = get_settings()
    limits = _limits(identifier, ip)
    async with db.begin():
        programme = await active_programme(db)
        participant = await _find_participant(db, programme, identifier)
        await _reserve_attempt(
            db, limits, "Too many unsuccessful attempts. Wait a few minutes and try again."
        )

    if participant is None:
        await verify_password(None, password)  # equalise timing for unknown accounts
        await _record_failure(db, limits)
        raise invalid_credentials()

    verified_version = participant.credential_version
    if not await verify_password(participant.password_hash, password):
        await _record_failure(db, limits)
        raise invalid_credentials()

    async with db.begin():
        locked = await lock_participant(db, participant.id)
        if locked.credential_version != verified_version:
            raise invalid_credentials()
        if locked.disabled_at is not None:
            raise AppError(
                "ACCOUNT_DISABLED", "This account has been disabled. Contact the organisers for help.", 403
            )
        programme_row = (
            await db.execute(
                select(Programme)
                .where(Programme.id == locked.programme_id)
                .execution_options(populate_existing=True)
            )
        ).scalar_one()
        now = await server_now(db)
        phase = login_phase(programme_row, now)
        if phase == "before_open":
            raise AppError(
                "LOGIN_NOT_OPEN",
                "Sign-in has not opened yet.",
                403,
                {
                    "opens_at": programme_row.login_opens_at.isoformat()
                    if programme_row.login_opens_at
                    else None
                },
            )
        if phase == "closed":
            raise AppError("LOGIN_CLOSED", "Sign-in for this programme has closed.", 403)

        # A voter may be signed in on several devices at once. Votes stay one per ballot per
        # participant, whichever device sends them. Sessions that have expired are revoked here.
        active = (
            (
                await db.execute(
                    select(ParticipantSession).where(
                        ParticipantSession.participant_id == locked.id,
                        ParticipantSession.revoked_at.is_(None),
                    )
                )
            )
            .scalars()
            .all()
        )
        presented_hash = token_hash(presented_token) if presented_token else None
        for existing in active:
            if existing.expires_at <= now:
                existing.revoked_at = now
                existing.revoke_reason = "expired"
            elif presented_hash is not None and existing.token_hash == presented_hash:
                # The same browser signing in again keeps its session.
                await clear(db, limits[0].key)
                return LoginResult(None, existing.id, locked, reused=True)
        await db.flush()

        token = new_token()
        session = ParticipantSession(
            id=uuid.uuid4(),
            participant_id=locked.id,
            programme_id=locked.programme_id,
            token_hash=token_hash(token),
            expires_at=now + timedelta(hours=settings.session_ttl_hours),
            user_agent=user_agent[:200],
        )
        db.add(session)
        await db.flush()
        if locked.activated_at is None:
            locked.activated_at = now  # first sign-in
        await clear(db, limits[0].key)
        await audit(
            db,
            Actor("participant", locked.id, locked.user_code, ip),
            "participant.login",
            programme_id=locked.programme_id,
            target_type="session",
            target_id=session.id,
        )
        return LoginResult(token, session.id, locked, reused=False)


async def participant_logout(db: AsyncSession, token: str | None) -> None:
    if not token:
        return
    async with db.begin():
        row = (
            await db.execute(
                select(ParticipantSession)
                .where(ParticipantSession.token_hash == token_hash(token))
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        ).scalar_one_or_none()
        if row is None or row.revoked_at is not None:
            return
        row.revoked_at = await server_now(db)
        row.revoke_reason = "logout"
        await emit(
            db,
            row.programme_id,
            "session",
            {"pid": str(row.participant_id), "sid": str(row.id), "r": "logout"},
        )


# ---- admins -------------------------------------------------------------------


async def admin_login(
    db: AsyncSession, email: str, password: str, ip: str, user_agent: str
) -> tuple[str, AdminUser]:
    settings = get_settings()
    key = email.strip().lower()[:200]
    limits = (
        Limit(f"admin_fail:acct:{key}", 10, 900),
        Limit(f"admin_fail:ip:{ip}", 50, 900),
    )
    async with db.begin():
        await enforce(
            db, *limits, message="Too many unsuccessful attempts. Wait a few minutes and try again."
        )
        admin = (await db.execute(select(AdminUser).where(AdminUser.email == key))).scalar_one_or_none()
    ok = await verify_password(admin.password_hash if admin else None, password)
    if admin is None or not ok or admin.disabled_at is not None:
        await _record_failure(db, limits)
        raise AppError("INVALID_CREDENTIALS", "That email or password is not right.", 401)
    token = new_token()
    async with db.begin():
        now = await server_now(db)
        db.add(
            AdminSession(
                admin_id=admin.id,
                token_hash=token_hash(token),
                expires_at=now + timedelta(hours=settings.admin_session_ttl_hours),
                user_agent=user_agent[:200],
            )
        )
        await db.execute(
            text("UPDATE admin_users SET last_login_at = now() WHERE id = :id"), {"id": admin.id}
        )
        await clear(db, limits[0].key)
        await audit(db, Actor("admin", admin.id, admin.email, ip), "admin.login")
    return token, admin


async def admin_logout(db: AsyncSession, token: str | None) -> None:
    if not token:
        return
    async with db.begin():
        ended = (
            await db.execute(
                update(AdminSession)
                .where(AdminSession.token_hash == token_hash(token), AdminSession.revoked_at.is_(None))
                .values(revoked_at=text("now()"), revoke_reason="logout")
                .returning(AdminSession.id, AdminSession.admin_id)
            )
        ).first()
        if ended is not None:
            # Close this session's admin sockets on every API process right away.
            await emit(
                db, None, "admin_session", {"aid": str(ended.admin_id), "sid": str(ended.id), "r": "logout"}
            )
