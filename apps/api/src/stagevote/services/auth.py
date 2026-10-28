"""Participant and admin authentication.

Participants sign in with the personal link from their latest invitation email: there is
no password, activation step or self-service reset. The link carries a 256-bit token of
which only the SHA-256 is stored. Opening the link only shows the sign-in page (and asks
`check_link` what to offer); the participant's tap on the button posts the token to
`enter_with_link`, so mail scanners that merely open links use nothing up.

One browser per link: the first browser that enters with a link is bound to it, by the
SHA-256 of that browser's long-lived device cookie, and only that browser can enter with
the link again (after signing out, or once its session has expired). Any other browser is
refused with LINK_IN_USE. An organiser re-sends the invitation to replace the link, which
releases the binding and signs the participant out everywhere. The device cookie on its
own grants nothing: requests and WebSockets are authenticated by the session cookie alone.

Entering locks the participant row FOR UPDATE, so two browsers tapping the same link at
once run one after another: the first is bound, the second is refused.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import func, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import get_settings
from ..errors import AppError
from ..models import AdminSession, AdminUser, Participant, ParticipantSession, Programme
from ..ratelimit import Limit, clear, enforce, hit
from ..security import is_token_shaped, new_token, token_hash, verify_password
from ..state import Actor, audit, emit
from .invitations import revoke_participant_sessions
from .programmes import login_phase, server_now

TOO_MANY_ATTEMPTS = "Too many unsuccessful attempts. Wait a few minutes and try again."


def link_invalid() -> AppError:
    return AppError(
        "LINK_INVALID",
        "This sign-in link is not valid any more. Open the newest invitation email, or ask the "
        "help desk to send you a new one.",
        401,
    )


def link_in_use() -> AppError:
    return AppError(
        "LINK_IN_USE",
        "This link is already in use on another phone or browser. If that was you in another app "
        "(for example inside your email app), open the link there. Otherwise ask the help desk to "
        "send you a new invitation.",
        403,
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
    token: str | None  # the new session token; None when this browser's session was reused
    session_id: uuid.UUID
    expires_at: datetime
    reused: bool
    device_token: str | None  # a new device cookie for a browser that sent none


def _ip_limit(ip: str) -> Limit:
    """Unknown links per client address. Only failures count, and the limit is generous,
    because a whole venue may share one public address. A refusal on another device is
    not a failure here: it is the link's owner (or someone they shared it with)."""
    s = get_settings()
    return Limit(f"login_fail:ip:{ip}", s.login_failures_per_ip, s.login_ip_window_seconds)


async def _linked_participant(db: AsyncSession, token: str) -> Participant | None:
    """The participant whose current link carries this token, in the active programme."""
    if not is_token_shaped(token):
        return None
    return (
        await db.execute(
            select(Participant)
            .join(Programme, Programme.id == Participant.programme_id)
            .where(Participant.link_token_hash == token_hash(token), Programme.is_active.is_(True))
        )
    ).scalar_one_or_none()


async def check_link(
    db: AsyncSession, token: str, *, presented_session: str | None, presented_device: str | None, ip: str
) -> str:
    """What the sign-in page should offer for this link: ready | signed_in | in_use |
    invalid | disabled.

    It never binds a browser, opens a session or writes to the audit log; an unknown link
    only counts against the client address.
    """
    limit = _ip_limit(ip)
    async with db.begin():
        await enforce(db, limit, message=TOO_MANY_ATTEMPTS)
        participant = await _linked_participant(db, token)
        if participant is None:
            await hit(db, limit)
            return "invalid"
        if participant.disabled_at is not None:
            return "disabled"
        if presented_session is not None:
            live = (
                await db.execute(
                    select(ParticipantSession.id).where(
                        ParticipantSession.token_hash == token_hash(presented_session),
                        ParticipantSession.participant_id == participant.id,
                        ParticipantSession.revoked_at.is_(None),
                        ParticipantSession.expires_at > func.clock_timestamp(),
                    )
                )
            ).scalar_one_or_none()
            if live is not None:
                return "signed_in"
        bound = participant.link_device_hash
        if bound is not None and (presented_device is None or bound != token_hash(presented_device)):
            return "in_use"
        return "ready"


async def enter_with_link(
    db: AsyncSession,
    token: str,
    *,
    presented_session: str | None,
    presented_device: str | None,
    ip: str,
    user_agent: str,
) -> LoginResult:
    """Sign in with a personal link ("Enter the arena").

    The first browser to enter is bound to the link. Coming back from that browser reuses
    its live session or opens a new one; any other browser is refused with LINK_IN_USE.
    Outside the login window nothing is bound, so an early tap never uses the link up.
    """
    limit = _ip_limit(ip)
    async with db.begin():
        await enforce(db, limit, message=TOO_MANY_ATTEMPTS)
        participant = await _linked_participant(db, token)
        if participant is None:
            await hit(db, limit)  # committed with this transaction
    if participant is None:
        raise link_invalid()

    # A browser that sent no device cookie gets one now; it is bound only if entry succeeds.
    device_token = presented_device or new_token()
    result: LoginResult | None = None
    async with db.begin():
        locked = await lock_participant(db, participant.id)
        if locked.link_token_hash != token_hash(token):
            raise link_invalid()  # a newer invitation replaced the link meanwhile
        if locked.disabled_at is not None:
            raise AppError(
                "ACCOUNT_DISABLED", "This account has been disabled. Contact the organisers for help.", 403
            )
        programme = (
            await db.execute(
                select(Programme)
                .where(Programme.id == locked.programme_id)
                .execution_options(populate_existing=True)
            )
        ).scalar_one()
        now = await server_now(db)
        phase = login_phase(programme, now)
        if phase == "before_open":
            raise AppError(
                "LOGIN_NOT_OPEN",
                "Sign-in has not opened yet.",
                403,
                {"opens_at": programme.login_opens_at.isoformat() if programme.login_opens_at else None},
            )
        if phase == "closed":
            raise AppError("LOGIN_CLOSED", "Sign-in for this programme has closed.", 403)

        actor = Actor("participant", locked.id, locked.user_code, ip)
        device_hash = token_hash(device_token)
        if locked.link_device_hash is not None and locked.link_device_hash != device_hash:
            # Recorded for the help desk, then refused once this transaction has committed.
            # Never counted against the client address.
            await audit(
                db,
                actor,
                "participant.link_refused",
                programme_id=locked.programme_id,
                target_type="participant",
                target_id=locked.id,
                details={"reason": "other_device"},
            )
        else:
            if locked.link_device_hash is None:
                # The first browser to enter with this link keeps it.
                locked.link_device_hash = device_hash
                locked.link_bound_at = now
                await audit(
                    db,
                    actor,
                    "participant.link_bound",
                    programme_id=locked.programme_id,
                    target_type="participant",
                    target_id=locked.id,
                    details={"user_agent": user_agent[:200]},
                )
            result = await _open_session(
                db, locked, actor, presented_session=presented_session, now=now, user_agent=user_agent
            )
            if presented_device is None:
                result.device_token = device_token
    if result is None:
        raise link_in_use()
    return result


async def _open_session(
    db: AsyncSession,
    locked: Participant,
    actor: Actor,
    *,
    presented_session: str | None,
    now: datetime,
    user_agent: str,
) -> LoginResult:
    """Reuse the browser's live session, or end every other one and open a new one.
    The caller holds the participant lock."""
    settings = get_settings()
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
    presented_hash = token_hash(presented_session) if presented_session else None
    reused: ParticipantSession | None = None
    for existing in active:
        if existing.expires_at <= now:
            existing.revoked_at = now
            existing.revoke_reason = "expired"
        elif presented_hash is not None and existing.token_hash == presented_hash:
            reused = existing
    await db.flush()
    if reused is not None:
        # The same browser entering again keeps its session.
        return LoginResult(None, reused.id, reused.expires_at, reused=True, device_token=None)

    # A link has one browser, so any other live session (for example one whose response
    # never reached the browser) ends here; the live signal names exactly those sessions.
    await revoke_participant_sessions(db, locked, "replaced", now)
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
    await audit(
        db,
        actor,
        "participant.login",
        programme_id=locked.programme_id,
        target_type="session",
        target_id=session.id,
        details={"via": "link"},
    )
    return LoginResult(token, session.id, session.expires_at, reused=False, device_token=None)


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


async def _record_failure(db: AsyncSession, limits: tuple[Limit, Limit]) -> None:
    """Count a failed organiser sign-in against the client address."""
    async with db.begin():
        await hit(db, limits[1])


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
