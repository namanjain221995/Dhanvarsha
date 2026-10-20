"""Participant import: parsing, validation, duplicate detection and race-safe commits."""

from __future__ import annotations

import asyncio
import random
import re
import uuid
from collections.abc import AsyncIterator, Callable
from typing import Any

import pytest

from helpers import AdminClient, Factory, World
from stagevote.services import participants as participants_service

PREVIEW = "/participants/import/preview"
COMMIT = "/participants/import/commit"


def by_row(body: dict[str, Any]) -> dict[int, dict[str, Any]]:
    return {r["row"]: r for r in body["rows"]}


async def stored(factory: Factory, programme_id: uuid.UUID) -> dict[str, Any]:
    rows = await factory.fetch(
        "SELECT email, email_normalized, display_name, user_code FROM participants WHERE programme_id = :p",
        p=programme_id,
    )
    return {r.email_normalized: r for r in rows}


async def test_preview_parses_pasted_formats_and_flags_problem_rows(world: World) -> None:
    admin = await world.admin()
    text = "\n".join(
        [
            "Asha Rao <Asha.Rao@Example.COM>",  # 1 name <email>
            "bob@example.com, Bob Builder",  # 2 email, name
            "not-an-email",  # 3 invalid
            "ASHA.RAO@EXAMPLE.COM",  # 4 same person as row 1 (case-insensitive)
            "asharao@example.com",  # 5 dots are significant
            "asha.rao+vote@example.com",  # 6 +tags are significant
            "",  # 7 blank lines are skipped
            "carol@example.com;Carol Singer",  # 8 semicolon separator
            "dan@",  # 9 invalid
        ]
    )
    resp = await admin.post(PREVIEW, {"text": text})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    rows = by_row(body)
    assert body["summary"] == {"new": 5, "invalid": 2, "duplicate_in_input": 1, "already_imported": 0}
    assert set(rows) == {1, 2, 3, 4, 5, 6, 8, 9}

    assert (rows[1]["status"], rows[1]["email"], rows[1]["display_name"]) == (
        "new",
        "Asha.Rao@example.com",
        "Asha Rao",
    )
    assert (rows[2]["status"], rows[2]["email"], rows[2]["display_name"]) == (
        "new",
        "bob@example.com",
        "Bob Builder",
    )
    assert rows[3]["status"] == "invalid" and rows[3]["reason"]
    assert rows[4]["status"] == "duplicate_in_input" and "row 1" in rows[4]["reason"]
    assert (rows[5]["status"], rows[5]["email"]) == ("new", "asharao@example.com")
    assert (rows[6]["status"], rows[6]["email"]) == ("new", "asha.rao+vote@example.com")
    assert (rows[8]["status"], rows[8]["display_name"]) == ("new", "Carol Singer")
    assert rows[9]["status"] == "invalid"
    # A preview never writes anything.
    assert await stored(world.factory, world.programme_id) == {}


async def test_commit_stores_new_rows_once_and_matches_case_insensitively(world: World) -> None:
    admin = await world.admin()
    first = "\n".join(
        [
            "Asha Rao <Asha.Rao@Example.COM>",
            "asharao@example.com",
            "asha.rao+vote@example.com",
            "bob@example.com",
        ]
    )
    resp = await admin.post(COMMIT, {"text": first})
    assert resp.status_code == 200, resp.text
    assert resp.json()["created"] == 4

    rows = await stored(world.factory, world.programme_id)
    assert set(rows) == {
        "asha.rao@example.com",
        "asharao@example.com",
        "asha.rao+vote@example.com",
        "bob@example.com",
    }
    # The address is stored as entered (local part case kept); only the key is lowercased.
    assert rows["asha.rao@example.com"].email == "Asha.Rao@example.com"
    codes = [r.user_code for r in rows.values()]
    assert len(set(codes)) == 4 and all(re.fullmatch(r"SV-[ABCDEFGHJKMNPQRSTUVWXYZ2-9]{7}", c) for c in codes)

    second = "\n".join(
        [
            "asha.rao@EXAMPLE.com",  # already imported, different case
            "ASHARAO@example.com",  # already imported
            "Asha.Rao+VOTE@example.com",  # already imported (tag kept, case ignored)
            "a.sharao@example.com",  # different dot position: a different person
            "asha.rao+other@example.com",  # different tag: a different person
        ]
    )
    preview = await admin.post(PREVIEW, {"text": second})
    assert [r["status"] for r in preview.json()["rows"]] == [
        "already_imported",
        "already_imported",
        "already_imported",
        "new",
        "new",
    ]
    resp = await admin.post(COMMIT, {"text": second})
    assert resp.json()["created"] == 2
    assert len(await stored(world.factory, world.programme_id)) == 6


async def test_csv_with_header_maps_columns_and_reports_bad_rows(world: World) -> None:
    admin = await world.admin()
    csv_text = (
        "Email,Full_Name,Team\r\n"
        'dave@example.com,"Dave, Jr.",Owls\r\n'
        "erin@example.com,Erin Example,Kettle\r\n"
        "not-valid,Bad Row,X\r\n"
        ",Missing Address,Y\r\n"
        "DAVE@example.com,Dave Again,Z\r\n"
    )
    resp = await admin.post(PREVIEW, {"text": csv_text})
    rows = by_row(resp.json())
    assert (rows[2]["status"], rows[2]["email"], rows[2]["display_name"]) == (
        "new",
        "dave@example.com",
        "Dave, Jr.",
    )
    assert (rows[3]["status"], rows[3]["display_name"]) == ("new", "Erin Example")
    assert rows[4]["status"] == "invalid"
    assert rows[5]["status"] == "invalid"
    assert rows[6]["status"] == "duplicate_in_input"

    # The same file uploaded as UTF-8 with a byte-order mark parses identically.
    upload = await admin.http.post(
        "/api/v1/admin/participants/import/preview-file",
        files={"file": ("guests.csv", "﻿".encode() + csv_text.encode(), "text/csv")},
        headers={"X-CSRF-Token": admin.csrf},
    )
    assert upload.status_code == 200, upload.text
    assert [r["status"] for r in upload.json()["rows"]] == [r["status"] for r in resp.json()["rows"]]

    commit = await admin.post(COMMIT, {"text": csv_text})
    assert commit.json()["created"] == 2
    assert set(await stored(world.factory, world.programme_id)) == {"dave@example.com", "erin@example.com"}


@pytest.fixture
async def overlapping_inserts(
    factory: Factory, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[Callable[[int], None]]:
    """Force concurrent import commits to INSERT at the same time and slowly.

    A barrier releases the requests together once validation is done, and a test-only
    trigger (throwaway database) makes each inserted row take ~2 ms, so the INSERT
    statements of concurrent commits are guaranteed to overlap inside PostgreSQL.
    """
    await factory.execute(
        "CREATE FUNCTION test_slow_insert() RETURNS trigger LANGUAGE plpgsql AS "
        "$$ BEGIN PERFORM pg_sleep(0.002); RETURN NEW; END $$"
    )
    await factory.execute(
        "CREATE TRIGGER test_slow_participants BEFORE INSERT ON participants "
        "FOR EACH ROW EXECUTE FUNCTION test_slow_insert()"
    )

    def arm(parties: int) -> None:
        barrier = asyncio.Barrier(parties)
        original = participants_service.preview_import

        async def preview_then_wait(db: Any, programme_id: uuid.UUID, text: str) -> Any:
            rows = await original(db, programme_id, text)
            await asyncio.wait_for(barrier.wait(), timeout=10)
            return rows

        monkeypatch.setattr(participants_service, "preview_import", preview_then_wait)

    try:
        yield arm
    finally:
        await factory.execute("DROP TRIGGER IF EXISTS test_slow_participants ON participants")
        await factory.execute("DROP FUNCTION IF EXISTS test_slow_insert()")


async def _concurrent_commits(
    world: World, arm: Callable[[int], None], texts: list[str], addresses: list[str]
) -> None:
    admins: list[AdminClient] = [await world.admin(fresh=True) for _ in texts]
    arm(len(texts))
    responses = await asyncio.gather(
        *(a.post(COMMIT, {"text": t}) for a, t in zip(admins, texts, strict=True))
    )
    for resp in responses:
        assert resp.status_code == 200, resp.text
    # Every address was created by exactly one request; the others saw it as imported.
    creators: dict[str, int] = {}
    for resp in responses:
        for row in resp.json()["rows"]:
            assert row["status"] in ("new", "already_imported"), row
            if row["status"] == "new":
                key = row["email"].lower()
                creators[key] = creators.get(key, 0) + 1
    assert creators == {a.lower(): 1 for a in addresses}
    assert sum(r.json()["created"] for r in responses) == len(addresses)

    counts = await world.factory.fetchrow(
        "SELECT count(*) AS n, count(DISTINCT email_normalized) AS d FROM participants WHERE programme_id = :p",
        p=world.programme_id,
    )
    assert (counts.n, counts.d) == (len(addresses), len(addresses))


async def test_two_concurrent_commits_of_the_same_list(
    world: World, overlapping_inserts: Callable[[int], None]
) -> None:
    tag = uuid.uuid4().hex[:6]
    addresses = [f"guest{i}.{tag}@example.com" for i in range(60)]
    text = "\n".join(addresses)
    await _concurrent_commits(world, overlapping_inserts, [text, text], addresses)


@pytest.mark.parametrize("seed", [1, 2])
async def test_concurrent_commits_in_different_order_and_case(
    world: World, overlapping_inserts: Callable[[int], None], seed: int
) -> None:
    """Overlapping imports by several organisers must neither duplicate nor fail.

    Regression: rows were inserted in input order, so two imports of the same people in
    a different order deadlocked in PostgreSQL and one organiser got a 500.
    """
    tag = uuid.uuid4().hex[:6]
    addresses = [f"Guest{i}.{tag}@example.com" for i in range(60)]
    shuffled = addresses[:]
    random.Random(seed).shuffle(shuffled)
    texts = [
        "\n".join(addresses),
        "\n".join(a.upper() for a in reversed(addresses)),
        "\n".join(f"Guest {i} <{a.lower()}>" for i, a in enumerate(shuffled)),
    ]
    await _concurrent_commits(world, overlapping_inserts, texts, addresses)
