"""Operator command line: `stagevote <command>`.

bootstrap        migrate, create the default programme and the first admin (idempotent)
migrate          apply database migrations only
create-admin     create an admin interactively (never resets an existing one unless asked)
worker           run the background worker
seed-demo        fill a pristine programme with 14 demo rounds and synthetic participants
seed-rounds      add N blank events to fill in later (default 14; allowed in production)
seed-sample-content  fill empty event descriptions and ballots with sample teams to replace later
seed-loadtest    create an isolated synthetic programme for load testing (non-production);
                 writes one sign-in link token per synthetic account
sign-in-link     give one participant a new sign-in link and print it instead of emailing it
                 (non-production; the previous link stops working)
activate-programme   switch the active programme by slug
reconcile        verify stored votes against receipts and aggregates
check-privileges verify the runtime role cannot modify votes
"""

from __future__ import annotations

import argparse
import asyncio
import getpass
import os
import sys

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from .config import get_settings
from .db import build_engine


def _owner_session() -> tuple[async_sessionmaker[AsyncSession], object]:
    settings = get_settings()
    engine = build_engine(settings, settings.migration_url, pool_size=2)
    return async_sessionmaker(engine, expire_on_commit=False), engine


def migrate() -> None:
    from alembic import command
    from alembic.config import Config

    here = os.path.dirname(os.path.abspath(__file__))
    candidates = [os.path.join(here, "..", "..", "alembic.ini"), "/app/alembic.ini", "alembic.ini"]
    ini = next((c for c in candidates if os.path.exists(c)), None)
    if ini is None:
        raise SystemExit("alembic.ini not found")
    cfg = Config(ini)
    cfg.attributes["url"] = get_settings().migration_url
    cfg.attributes["app_role"] = get_settings().db_app_role
    command.upgrade(cfg, "head")


async def _ensure_programme(db: AsyncSession) -> None:
    from .models import Programme

    existing = (await db.execute(select(func.count()).select_from(Programme))).scalar_one()
    if existing:
        return
    settings = get_settings()
    db.add(
        Programme(
            slug="main",
            is_active=True,
            name=f"{settings.product_name} programme",
            timezone=settings.programme_timezone,
        )
    )
    print("Created the default programme (edit it in the admin settings).")


async def _ensure_admin(db: AsyncSession, email: str | None, password: str | None) -> None:
    from .models import AdminUser
    from .security import hash_password, password_problem

    if not email:
        count = (await db.execute(select(func.count()).select_from(AdminUser))).scalar_one()
        if not count:
            print("No admin exists. Set ADMIN_BOOTSTRAP_EMAIL/PASSWORD or run `stagevote create-admin`.")
        return
    key = email.strip().lower()
    exists = (await db.execute(select(AdminUser).where(AdminUser.email == key))).scalar_one_or_none()
    if exists is not None:
        print(f"Admin {key} already exists; its password was not changed.")
        return
    if not password:
        print(
            f"Admin {key} does not exist and ADMIN_BOOTSTRAP_PASSWORD is empty; run `stagevote create-admin`."
        )
        return
    problem = password_problem(password)
    if problem or len(password) < 12:
        raise SystemExit(f"ADMIN_BOOTSTRAP_PASSWORD is too weak: {problem or 'use at least 12 characters'}")
    db.add(AdminUser(email=key, display_name="Organiser", password_hash=await hash_password(password)))
    print(f"Created admin {key}.")


def bootstrap() -> None:
    """Migrate, then create the default programme and first admin if missing."""
    get_settings().validate_for_runtime()
    migrate()
    asyncio.run(_bootstrap_data())


RUNTIME_GRANTS = """
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{role}') THEN
        GRANT USAGE ON SCHEMA public TO {role};
        GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO {role};
        GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO {role};
        REVOKE UPDATE, DELETE, TRUNCATE ON votes FROM {role};
        REVOKE UPDATE, DELETE, TRUNCATE ON audit_log FROM {role};
        REVOKE ALL ON alembic_version FROM {role};
        GRANT SELECT ON alembic_version TO {role};
    ELSE
        RAISE WARNING 'runtime role {role} does not exist; the API cannot connect until it does';
    END IF;
END
$$
"""


async def apply_runtime_grants(db: AsyncSession) -> None:
    """(Re)apply the runtime role's privileges. Idempotent; needed after a restore with
    --no-privileges and for tables added by later migrations."""
    role = get_settings().db_app_role
    if not role.replace("_", "").isalnum():
        raise SystemExit(f"unsafe DB_APP_ROLE: {role!r}")
    await db.execute(text(RUNTIME_GRANTS.format(role=role)))


async def _bootstrap_data() -> None:
    settings = get_settings()
    maker, engine = _owner_session()
    async with maker() as db, db.begin():
        await apply_runtime_grants(db)
        await _ensure_programme(db)
        password = (
            settings.admin_bootstrap_password.get_secret_value()
            if settings.admin_bootstrap_password
            else None
        )
        await _ensure_admin(db, settings.admin_bootstrap_email, password)
    await engine.dispose()  # type: ignore[attr-defined]


async def create_admin(email: str, reset: bool) -> None:
    from .models import AdminUser
    from .security import hash_password, password_problem

    password = os.environ.get("STAGEVOTE_NEW_ADMIN_PASSWORD") or getpass.getpass("New admin password: ")
    if password_problem(password) or len(password) < 12:
        raise SystemExit("Use at least 12 characters that are not easy to guess.")
    maker, engine = _owner_session()
    async with maker() as db, db.begin():
        key = email.strip().lower()
        admin = (await db.execute(select(AdminUser).where(AdminUser.email == key))).scalar_one_or_none()
        if admin is not None and not reset:
            raise SystemExit("That admin exists. Pass --reset-password to change its password deliberately.")
        hashed = await hash_password(password)
        if admin is None:
            db.add(AdminUser(email=key, display_name="Organiser", password_hash=hashed))
        else:
            admin.password_hash = hashed
            await db.execute(
                text(
                    "UPDATE admin_sessions SET revoked_at = now(), revoke_reason = 'password_reset' "
                    "WHERE admin_id = :a AND revoked_at IS NULL"
                ),
                {"a": admin.id},
            )
    await engine.dispose()  # type: ignore[attr-defined]
    print(f"Admin {email} saved.")


async def activate_programme(slug: str) -> None:
    from .models import Programme

    maker, engine = _owner_session()
    async with maker() as db, db.begin():
        target = (await db.execute(select(Programme).where(Programme.slug == slug))).scalar_one_or_none()
        if target is None:
            raise SystemExit(f"No programme with slug {slug!r}")
        await db.execute(text("UPDATE programmes SET is_active = false WHERE is_active"))
        await db.flush()
        target.is_active = True
        await db.execute(
            text("UPDATE programmes SET state_version = state_version + 1 WHERE id = :p"), {"p": target.id}
        )
    await engine.dispose()  # type: ignore[attr-defined]
    print(f"Active programme is now {slug}.")


async def sign_in_link(email: str) -> str:
    """Rotate one participant's sign-in link exactly as an invitation does (new token,
    binding released, sessions signed out, pending invitation emails superseded) but
    print the link instead of emailing it. For development, screenshots and tests."""
    from .models import Participant, Programme
    from .services.invitations import replace_link
    from .services.invitations import sign_in_link as link_for
    from .services.participants import normalize_email
    from .services.programmes import server_now
    from .state import Actor, audit

    settings = get_settings()
    if settings.is_production:
        raise SystemExit(
            "Refusing to print sign-in links in production (APP_ENV=production). "
            "Send the invitation from the organiser console instead."
        )
    try:
        _, key = normalize_email(email)
    except ValueError as exc:
        raise SystemExit(f"{email!r} is not a valid email address: {exc}") from exc
    maker, engine = _owner_session()
    async with maker() as db, db.begin():
        programme = (
            await db.execute(select(Programme).where(Programme.is_active.is_(True)))
        ).scalar_one_or_none()
        if programme is None:
            raise SystemExit("Run `stagevote bootstrap` first.")
        participant = (
            await db.execute(
                select(Participant)
                .where(Participant.programme_id == programme.id, Participant.email_normalized == key)
                .with_for_update()
            )
        ).scalar_one_or_none()
        if participant is None:
            raise SystemExit(f"No participant {key} in the active programme {programme.slug!r}.")
        if participant.disabled_at is not None:
            raise SystemExit(f"{key} is disabled. Enable the account first.")
        token, signed_out = await replace_link(db, participant, await server_now(db))
        await audit(
            db,
            Actor.system("cli"),
            "participant.link_issued",
            programme_id=programme.id,
            target_type="participant",
            target_id=participant.id,
            details={"via": "cli", "sessions_signed_out": signed_out},
        )
    await engine.dispose()  # type: ignore[attr-defined]
    print(
        f"New sign-in link for {key}. The previous link no longer works; {signed_out} session(s) "
        "signed out. It opens on the first browser that uses it:",
        file=sys.stderr,
    )
    link = link_for(settings.app_base_url, token)
    print(link)
    return link


async def reconcile(slug: str | None) -> int:
    """Check that aggregates equal the stored votes and that no duplicates exist.

    Typed answers are rows in votes too: they are counted on their own, and only on text
    ballots. The nominee tallies must add up to the votes that name a nominee.
    """
    maker, engine = _owner_session()
    async with maker() as db, db.begin():
        where = "pr.slug = :slug" if slug else "pr.is_active"
        params = {"slug": slug} if slug else {}
        programme_id = (
            await db.execute(text(f"SELECT id FROM programmes pr WHERE {where}"), params)  # noqa: S608 - only whitelisted constants are interpolated
        ).scalar_one()
        dupes = (
            await db.execute(
                text(
                    "SELECT count(*) FROM (SELECT participant_id, ballot_id FROM votes WHERE programme_id = :p "
                    "GROUP BY 1, 2 HAVING count(*) > 1) d"
                ),
                {"p": programme_id},
            )
        ).scalar_one()
        total, for_nominees, answers = (
            await db.execute(
                text(
                    "SELECT count(*), count(nominee_id), count(answer_text) FROM votes WHERE programme_id = :p"
                ),
                {"p": programme_id},
            )
        ).one()
        misplaced = (
            await db.execute(
                text(
                    "SELECT count(*) FROM votes v JOIN ballots b ON b.id = v.ballot_id WHERE v.programme_id = :p "
                    "AND (v.answer_text IS NOT NULL) <> (b.kind = 'text')"
                ),
                {"p": programme_id},
            )
        ).scalar_one()
        by_ballot = (
            await db.execute(
                text(
                    "SELECT b.title, e.name, count(v.id) FROM ballots b JOIN events e ON e.id = b.event_id "
                    "LEFT JOIN votes v ON v.ballot_id = b.id WHERE b.programme_id = :p GROUP BY b.id, b.title, e.name, "
                    "e.position ORDER BY e.position"
                ),
                {"p": programme_id},
            )
        ).all()
        tally_sum = (
            await db.execute(
                text(
                    "SELECT coalesce(sum(c), 0) FROM (SELECT count(*) c FROM nominees n JOIN ballots b ON b.id = n.ballot_id "
                    "JOIN votes v ON v.ballot_id = n.ballot_id AND v.nominee_id = n.id WHERE b.programme_id = :p "
                    "GROUP BY n.id) t"
                ),
                {"p": programme_id},
            )
        ).scalar_one()
        bad_refs = (
            await db.execute(
                text(
                    "SELECT count(*) FROM votes v JOIN nominees n ON n.id = v.nominee_id "
                    "WHERE v.programme_id = :p AND n.ballot_id <> v.ballot_id"
                ),
                {"p": programme_id},
            )
        ).scalar_one()
    await engine.dispose()  # type: ignore[attr-defined]
    print(f"votes stored:            {total}")
    print(f"votes for nominees:      {for_nominees}")
    print(f"sum of nominee tallies:  {tally_sum}")
    print(f"typed answers:           {answers}")
    print(f"duplicate (voter,ballot) {dupes}")
    print(f"cross-ballot references  {bad_refs}")
    print(f"wrong kind for ballot    {misplaced}")
    for title, event, count in by_ballot:
        print(f"  {event} / {title}: {count}")
    ok = (
        dupes == 0
        and bad_refs == 0
        and misplaced == 0
        and int(tally_sum) == int(for_nominees)
        and int(for_nominees) + int(answers) == int(total)
    )
    print("RECONCILED" if ok else "MISMATCH")
    return 0 if ok else 1


async def check_privileges() -> int:
    from sqlalchemy.exc import DBAPIError

    settings = get_settings()
    engine = build_engine(settings, settings.database_url, pool_size=1)
    failures = 0
    async with engine.connect() as conn:
        for statement in (
            "UPDATE votes SET receipt_code = receipt_code WHERE false",
            "DELETE FROM votes WHERE false",
            "TRUNCATE votes",
            "DELETE FROM audit_log WHERE false",
        ):
            try:
                async with conn.begin():
                    await conn.execute(text(statement))
                print(f"UNEXPECTEDLY ALLOWED: {statement}")
                failures += 1
            except DBAPIError:
                print(f"refused (good): {statement}")
        # The runtime role must still be able to do its job.
        for statement in (
            "SELECT count(*) FROM votes",
            "SELECT version_num FROM alembic_version",
            "SELECT count(*) FROM participants",
            "INSERT INTO rate_limit_buckets (key, window_started_at, count) VALUES ('privilege-check', now(), 1) "
            "ON CONFLICT (key) DO UPDATE SET count = 1",
            "DELETE FROM rate_limit_buckets WHERE key = 'privilege-check'",
        ):
            try:
                async with conn.begin():
                    await conn.execute(text(statement))
                print(f"allowed (good): {statement.split(' (')[0]}")
            except DBAPIError as exc:
                print(f"UNEXPECTEDLY REFUSED: {statement} ({type(exc.orig).__name__})")
                failures += 1
    await engine.dispose()
    return 1 if failures else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="stagevote", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("bootstrap")
    sub.add_parser("migrate")
    ca = sub.add_parser("create-admin")
    ca.add_argument("--email", required=True)
    ca.add_argument("--reset-password", action="store_true")
    sub.add_parser("worker")
    sd = sub.add_parser("seed-demo")
    sd.add_argument("--participants", type=int, default=40)
    sd.add_argument(
        "--activate-sample",
        type=int,
        default=0,
        help="give N synthetic participants a sign-in link (printed once) so they can sign in without an invitation",
    )
    sr = sub.add_parser("seed-rounds")
    sr.add_argument("--count", type=int, default=14)
    sub.add_parser("seed-sample-content")
    sl = sub.add_parser("seed-loadtest")
    sl.add_argument("--participants", type=int, default=500)
    sl.add_argument("--events", type=int, default=4)
    sl.add_argument(
        "--yes-synthetic",
        action="store_true",
        required=True,
        help="confirm this creates synthetic accounts in a non-production database",
    )
    sil = sub.add_parser("sign-in-link")
    sil.add_argument("--email", required=True)
    ap = sub.add_parser("activate-programme")
    ap.add_argument("--slug", required=True)
    rc = sub.add_parser("reconcile")
    rc.add_argument("--slug")
    sub.add_parser("check-privileges")
    args = parser.parse_args(argv)

    if args.command == "bootstrap":
        bootstrap()
    elif args.command == "migrate":
        migrate()
    elif args.command == "create-admin":
        asyncio.run(create_admin(args.email, args.reset_password))
    elif args.command == "worker":
        from .worker.runner import run_worker

        asyncio.run(run_worker())
    elif args.command == "seed-demo":
        from .seed import seed_demo

        asyncio.run(seed_demo(args.participants, args.activate_sample))
    elif args.command == "seed-rounds":
        from .seed import seed_rounds

        asyncio.run(seed_rounds(args.count))
    elif args.command == "seed-sample-content":
        from .seed import seed_sample_content

        asyncio.run(seed_sample_content())
    elif args.command == "seed-loadtest":
        from .seed import seed_loadtest

        asyncio.run(seed_loadtest(args.participants, args.events))
    elif args.command == "sign-in-link":
        asyncio.run(sign_in_link(args.email))
    elif args.command == "activate-programme":
        asyncio.run(activate_programme(args.slug))
    elif args.command == "reconcile":
        return asyncio.run(reconcile(args.slug))
    elif args.command == "check-privileges":
        return asyncio.run(check_privileges())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
