"""Opt-in demo and load-test data. Never runs automatically and refuses production.

seed-demo only fills a *pristine* active programme (no events, participants or votes),
so it can never overwrite or erase real data. seed-loadtest creates a separate,
clearly named programme of synthetic accounts.
"""

from __future__ import annotations

import hashlib
import math
import os
import random
import secrets
from datetime import UTC, datetime, timedelta

from PIL import Image, ImageDraw, ImageFont
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from .config import get_settings
from .db import build_engine
from .models import Asset, Ballot, Event, Nominee, Participant, Programme
from .security import hash_password, new_user_code
from .services.assets import store_generated_image

PALETTE = [
    ("#5b34c4", "#efe7ff"),
    ("#7a2a6e", "#fbe8f5"),
    ("#136f6a", "#e1f5f2"),
    ("#b8892b", "#fbf1dc"),
    ("#3b43b8", "#e6e8ff"),
    ("#b02a55", "#fde6ee"),
    ("#2b1631", "#efe6f1"),
    ("#8a5a00", "#fbefd6"),
]

TEAMS = [
    "Night Owls",
    "Velvet Static",
    "Copper Kettle",
    "Paper Planes",
    "Northern Lights",
    "Quiet Storm",
    "Bright Sparks",
    "Lantern Lab",
    "Monsoon Club",
    "Tidal Works",
    "Saffron Circuit",
    "Orbit Kitchen",
]
PEOPLE = [
    ("Asha Rao", "Night Owls"),
    ("Kabir Mehta", "Velvet Static"),
    ("Meera Pillai", "Copper Kettle"),
    ("Rohan Das", "Paper Planes"),
    ("Isha Kapoor", "Northern Lights"),
    ("Arjun Nair", "Quiet Storm"),
    ("Zoya Khan", "Bright Sparks"),
    ("Dev Iyer", "Lantern Lab"),
    ("Tara Singh", "Monsoon Club"),
    ("Nikhil Joshi", "Tidal Works"),
    ("Ananya Sen", "Saffron Circuit"),
    ("Farhan Ali", "Orbit Kitchen"),
    ("Lena Fischer", "Night Owls"),
    ("Sam Okafor", "Velvet Static"),
    ("Priya Menon", "Paper Planes"),
]

# Demo team rosters: each member of PEOPLE is in their own team, plus a few more names.
_EXTRA_MEMBERS = [
    "Kavya Reddy",
    "Yusuf Shaikh",
    "Neha Gupta",
    "Vikram Bose",
    "Riya Desai",
    "Omar Siddiqui",
    "Pooja Verma",
    "Aman Chopra",
    "Sneha Kulkarni",
    "Rahul Bhat",
    "Divya Nambiar",
    "Karan Malhotra",
]
TEAM_MEMBERS: dict[str, list[str]] = {
    team: [person for person, member_of in PEOPLE if member_of == team]
    + [_EXTRA_MEMBERS[i % len(_EXTRA_MEMBERS)], _EXTRA_MEMBERS[(i + 1) % len(_EXTRA_MEMBERS)]]
    for i, team in enumerate(TEAMS)
}

ROUNDS: list[tuple[str, str, list[tuple[str, str]]]] = [
    (
        "Opening Showcase",
        "Each team has four minutes to show what they built this season.",
        [("Best showcase", "team")],
    ),
    (
        "Lightning Pitches",
        "Ninety-second pitches. Vote for the idea you would fund tomorrow.",
        [("Best pitch", "team")],
    ),
    ("Design Sprint", "Teams redesigned the venue's wayfinding in two hours.", [("Best design", "team")]),
    (
        "Code Golf",
        "Shortest working solution wins the room's vote.",
        [("Most elegant solution", "individual")],
    ),
    (
        "Battle of the Bands",
        "Three songs each, one stage.",
        [("Best band", "team"), ("Best performer", "individual")],
    ),
    ("Product Demo Derby", "Live demos only. No slides allowed.", [("Best demo", "team")]),
    ("Data Story Challenge", "One dataset, many stories.", [("Clearest data story", "individual")]),
    ("Hardware Hack", "Prototypes built from the parts table.", [("Best prototype", "team")]),
    ("Comedy Open Mic", "Five minutes. Keep it kind.", [("Funniest set", "individual")]),
    ("Founders' Tank", "Teams pitch to the audience as the investor panel.", [("Most fundable", "team")]),
    ("UX Remix", "Fix a familiar app's most annoying screen.", [("Best remix", "team")]),
    (
        "Speed Quiz",
        "Rapid-fire rounds on stage. Vote for the best team spirit.",
        [("Best team spirit", "team")],
    ),
    ("Photo Walk", "Photos taken around the venue today.", [("Best photograph", "individual")]),
    (
        "Grand Finale",
        "The finalists return for one last performance.",
        [("Best team overall", "team"), ("Most valuable member", "individual")],
    ),
]

RULES = "One vote per ballot. Votes are final once submitted. Voting closes when the host ends the round."


def _font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    for path in (
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf",
    ):
        if os.path.exists(path):
            return ImageFont.truetype(path, size)
    return ImageFont.load_default(size=size)


def _initials(name: str) -> str:
    parts = [p for p in name.replace("'", "").split() if p[:1].isalnum()]
    return "".join(p[0] for p in parts[:2]).upper() or "?"


def event_logo(name: str, index: int) -> Image.Image:
    """A geometric badge drawn locally: no remote image service."""
    fg, bg = PALETTE[index % len(PALETTE)]
    size = 512
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.rounded_rectangle((0, 0, size - 1, size - 1), radius=120, fill=bg)
    rnd = random.Random(int(hashlib.sha256(name.encode()).hexdigest(), 16))
    motif = index % 4
    if motif == 0:
        for r in range(3):
            draw.ellipse((96 + r * 40, 96 + r * 40, 416 - r * 40, 416 - r * 40), outline=fg, width=14)
    elif motif == 1:
        for k in range(6):
            angle = math.pi * 2 * k / 6 + rnd.random() * 0.3
            x, y = 256 + 150 * math.cos(angle), 256 + 150 * math.sin(angle)
            draw.ellipse((x - 34, y - 34, x + 34, y + 34), fill=fg)
    elif motif == 2:
        draw.polygon([(256, 80), (432, 400), (80, 400)], outline=fg, width=18)
        draw.polygon([(256, 190), (350, 360), (162, 360)], fill=fg)
    else:
        for k in range(5):
            draw.rounded_rectangle(
                (96, 110 + k * 62, 416 - rnd.randint(0, 140), 146 + k * 62), radius=18, fill=fg
            )
    label = _initials(name)
    cx, cy = size - 120, size - 120
    draw.ellipse((cx - 92, cy - 92, cx + 92, cy + 92), fill=fg)
    small = _font(76)
    sbox = draw.textbbox((0, 0), label, font=small)
    draw.text(
        (cx - (sbox[2] - sbox[0]) / 2 - sbox[0], cy - (sbox[3] - sbox[1]) / 2 - sbox[1]),
        label,
        font=small,
        fill=bg,
    )
    return img


def avatar(name: str, index: int) -> Image.Image:
    fg, bg = PALETTE[(index * 3 + 1) % len(PALETTE)]
    size = 480
    img = Image.new("RGB", (size, size), bg)
    draw = ImageDraw.Draw(img)
    rnd = random.Random(name)
    for _ in range(4):
        r = rnd.randint(80, 220)
        x, y = rnd.randint(0, size), rnd.randint(0, size)
        draw.ellipse((x - r, y - r, x + r, y + r), fill=fg if rnd.random() > 0.5 else "#ffffff")
    draw.ellipse((150, 110, 330, 290), fill="#fffdf8")
    draw.ellipse((90, 300, 390, 600), fill="#fffdf8")
    return img


async def _store(db: AsyncSession, img: Image.Image, kind: str) -> Asset:
    key, width, height, data = store_generated_image(img, kind)
    asset = Asset(
        kind=kind,
        storage_key=key,
        content_type="image/webp",
        width=width,
        height=height,
        byte_size=len(data),
        sha256=hashlib.sha256(data).hexdigest(),
    )
    db.add(asset)
    await db.flush()
    return asset


def _refuse_production() -> None:
    if get_settings().is_production:
        raise SystemExit("Refusing to seed demo or load-test data in production (APP_ENV=production).")


def _maker() -> tuple[async_sessionmaker[AsyncSession], object]:
    settings = get_settings()
    engine = build_engine(settings, settings.migration_url, pool_size=2)
    return async_sessionmaker(engine, expire_on_commit=False), engine


async def _build_rounds(
    db: AsyncSession,
    programme: Programme,
    rounds: list[tuple[str, str, list[tuple[str, str]]]],
    with_art: bool = True,
) -> None:
    rnd = random.Random(7)
    for index, (name, description, ballots) in enumerate(rounds, start=1):
        logo = await _store(db, event_logo(name, index), "event_logo") if with_art else None
        event = Event(
            programme_id=programme.id,
            position=index,
            name=name,
            description=description,
            rules=RULES,
            logo_asset_id=logo.id if logo else None,
        )
        db.add(event)
        await db.flush()
        for b_index, (title, kind) in enumerate(ballots, start=1):
            ballot = Ballot(
                programme_id=programme.id, event_id=event.id, position=b_index, title=title, kind=kind
            )
            db.add(ballot)
            await db.flush()
            if kind == "team":
                choices = rnd.sample(TEAMS, k=rnd.randint(4, 6))
                for n_index, team in enumerate(choices, start=1):
                    db.add(
                        Nominee(
                            ballot_id=ballot.id,
                            position=n_index,
                            name=team,
                            affiliation="",
                            description=f"Season {rnd.randint(2, 6)} entrants.",
                            members=list(TEAM_MEMBERS.get(team, [])),
                        )
                    )
            else:
                choices_p = rnd.sample(PEOPLE, k=rnd.randint(4, 6))
                for n_index, (person, team) in enumerate(choices_p, start=1):
                    photo = None
                    if with_art and n_index % 2 == 1:
                        photo = await _store(db, avatar(person, n_index + index), "nominee_photo")
                    db.add(
                        Nominee(
                            ballot_id=ballot.id,
                            position=n_index,
                            name=person,
                            affiliation=team,
                            photo_asset_id=photo.id if photo else None,
                        )
                    )
            await db.flush()


async def seed_rounds() -> None:
    """Add the 14 sample rounds (names, rules, ballots, teams with members, logos) to the active
    programme as upcoming events: a starting line-up for organisers to edit in Admin > Events.

    Unlike seed-demo it creates no participants and changes no programme settings, so it may
    run in production. It is refused when the programme already has rounds.
    """
    from .services.live import begin_transition
    from .state import Actor, audit, bump_state

    maker, engine = _maker()
    async with maker() as db, db.begin():
        active = (
            await db.execute(select(Programme).where(Programme.is_active.is_(True)))
        ).scalar_one_or_none()
        if active is None:
            raise SystemExit("Run `stagevote bootstrap` first.")
        programme, _ = await begin_transition(db, active.id, None)
        existing = (
            await db.execute(
                select(func.count())
                .select_from(Event)
                .where(Event.programme_id == programme.id, Event.archived_at.is_(None))
            )
        ).scalar_one()
        if existing:
            raise SystemExit(
                f"The active programme already has {existing} rounds; nothing was added. "
                "Archive them first if you really want the sample line-up."
            )
        await _build_rounds(db, programme, ROUNDS)
        await bump_state(db, programme.id, live=True, reason="seed_rounds")
        await audit(
            db,
            Actor.system("cli"),
            "events.seeded",
            programme_id=programme.id,
            details={"events": len(ROUNDS)},
        )
        name = programme.name
    await engine.dispose()  # type: ignore[attr-defined]
    print(f"Added {len(ROUNDS)} upcoming rounds to {name}. Edit them in Admin > Events.")


async def seed_demo(participants: int, activate_sample: int) -> None:
    _refuse_production()
    maker, engine = _maker()
    async with maker() as db, db.begin():
        programme = (
            await db.execute(select(Programme).where(Programme.is_active.is_(True)))
        ).scalar_one_or_none()
        if programme is None:
            raise SystemExit("Run `stagevote bootstrap` first.")
        used = (
            await db.execute(
                text(
                    "SELECT (SELECT count(*) FROM events WHERE programme_id = :p) + "
                    "(SELECT count(*) FROM participants WHERE programme_id = :p)"
                ),
                {"p": programme.id},
            )
        ).scalar_one()
        if used:
            raise SystemExit(
                "The active programme already has events or participants. The demo seed only fills an "
                "empty programme and never changes existing data."
            )
        now = datetime.now(UTC).replace(second=0, microsecond=0)
        programme.name = "Innovation Night 2026"
        programme.slug = "demo-innovation-night"
        programme.description = (
            "Fourteen rounds of demos, music and pitches. Vote for your favourites as each round goes live."
        )
        programme.instructions = "Keep this page open. When the host opens a round, it appears at the top."
        programme.help_contact = "the help desk beside the stage"
        programme.login_opens_at = now - timedelta(minutes=15)
        programme.login_closes_at = now + timedelta(hours=10)
        programme.countdown_enabled = True
        logo_img = event_logo("Innovation Night", 0)
        programme.logo_asset_id = (await _store(db, logo_img, "programme_logo")).id
        await _build_rounds(db, programme, ROUNDS)
        first_names = [
            "Aditi",
            "Bhavin",
            "Chitra",
            "Daniel",
            "Esha",
            "Faisal",
            "Gauri",
            "Harsh",
            "Ira",
            "Jai",
            "Kiran",
            "Leela",
            "Manav",
            "Nisha",
            "Omar",
            "Pooja",
            "Qadir",
            "Riya",
            "Sahil",
            "Tanvi",
        ]
        sample_password = secrets.token_urlsafe(9) if activate_sample else None
        sample_hash = await hash_password(sample_password) if sample_password else None
        codes = []
        for i in range(1, participants + 1):
            name = f"{first_names[(i - 1) % len(first_names)]} Guest {i:03d}"
            p = Participant(
                programme_id=programme.id,
                email=f"guest{i:03d}@example.com",
                email_normalized=f"guest{i:03d}@example.com",
                display_name=name,
                user_code=new_user_code(),
                synthetic=True,
            )
            if i <= activate_sample:
                # Ready to sign in without an emailed invitation (demo convenience only).
                p.password_hash = sample_hash
                codes.append(p.email)
            db.add(p)
        await db.execute(
            text("UPDATE programmes SET state_version = state_version + 1 WHERE id = :p"), {"p": programme.id}
        )
    await engine.dispose()  # type: ignore[attr-defined]
    print(f"Demo programme ready: 14 rounds, {participants} synthetic participants (guestNNN@example.com).")
    print("Send invitations from Admin > Participants; they arrive in the local Mailpit inbox.")
    if codes:
        print(f"Sample accounts that can sign in now (password {sample_password}): {', '.join(codes)}")


async def seed_loadtest(participants: int, events: int) -> None:
    """Create and activate an isolated synthetic programme. Prints its credentials file."""
    _refuse_production()
    password = os.environ.get("LOADTEST_PASSWORD") or secrets.token_urlsafe(12)
    maker, engine = _maker()
    stamp = datetime.now(UTC).strftime("%Y%m%d%H%M%S")
    slug = f"loadtest-{stamp}"
    async with maker() as db, db.begin():
        await db.execute(text("UPDATE programmes SET is_active = false WHERE is_active"))
        await db.flush()
        now = datetime.now(UTC)
        programme = Programme(
            slug=slug,
            is_active=True,
            name=f"Load test {stamp}",
            timezone=get_settings().programme_timezone,
            login_opens_at=now - timedelta(minutes=5),
            login_closes_at=now + timedelta(hours=6),
        )
        db.add(programme)
        await db.flush()
        rounds = [
            (f"Load round {i}", "Synthetic load-test round.", [("Ballot", "team")])
            for i in range(1, events + 1)
        ]
        await _build_rounds(db, programme, rounds, with_art=False)
        hashed = await hash_password(password)
        lines = []
        for i in range(1, participants + 1):
            code = new_user_code()
            db.add(
                Participant(
                    programme_id=programme.id,
                    email=f"load{i:04d}@loadtest.example.com",
                    email_normalized=f"load{i:04d}@loadtest.example.com",
                    display_name=f"Load {i:04d}",
                    user_code=code,
                    password_hash=hashed,
                    activated_at=now,
                    synthetic=True,
                )
            )
            lines.append(code)
        count = (await db.execute(select(func.count()).select_from(Programme))).scalar_one()
    await engine.dispose()  # type: ignore[attr-defined]
    out = os.environ.get("LOADTEST_ACCOUNTS_FILE", "/tmp/loadtest-accounts.txt")  # noqa: S108 - synthetic data
    with open(out, "w", encoding="utf-8") as fh:  # noqa: ASYNC230 - one-off CLI write
        fh.write("\n".join(lines) + "\n")
    print(f"Load-test programme {slug} is now active ({count} programmes total).")
    print(f"{participants} synthetic accounts written to {out}; shared password from LOADTEST_PASSWORD.")
    if not os.environ.get("LOADTEST_PASSWORD"):
        print(f"Generated password: {password}")
