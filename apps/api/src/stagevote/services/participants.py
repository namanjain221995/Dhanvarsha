"""Participant import: parsing, normalisation, preview and race-safe commit."""

from __future__ import annotations

import csv
import io
import re
import unicodedata
import uuid
from dataclasses import asdict, dataclass
from typing import Any

from email_validator import EmailNotValidError, validate_email
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from ..errors import validation
from ..models import Participant
from ..security import new_user_code

MAX_IMPORT_ROWS = 5000
_SPLIT = re.compile(r"[,;\t]")
_ANGLE = re.compile(r"^(?P<name>.*?)<(?P<email>[^<>]+)>\s*$")


@dataclass
class ImportRow:
    row: int
    input: str
    email: str | None
    email_normalized: str | None
    display_name: str
    status: str  # new | invalid | duplicate_in_input | already_imported
    reason: str = ""

    def public(self) -> dict[str, Any]:
        data = asdict(self)
        data.pop("email_normalized")
        return data


def normalize_email(raw: str) -> tuple[str, str]:
    """Return (stored address, uniqueness key) or raise ValueError with a reason.

    The policy is deliberately conservative: trim whitespace and wrapping punctuation,
    apply Unicode NFC, validate syntax, lowercase and IDNA-normalise the domain, and
    compare case-insensitively. Dots and +tags are preserved because removing them is
    only correct for some providers.
    """
    candidate = unicodedata.normalize("NFC", raw).strip().strip("<>\"' ").strip()
    if not candidate:
        raise ValueError("Empty email address.")
    try:
        info = validate_email(candidate, check_deliverability=False)
    except EmailNotValidError as exc:
        raise ValueError(str(exc)) from exc
    stored = info.normalized
    return stored, stored.lower()


def _clean_name(value: str) -> str:
    name = " ".join(unicodedata.normalize("NFC", value).replace("\x00", "").split())
    return name.strip("\"' ")[:120]


def parse_import(text: str) -> list[tuple[int, str, str, str]]:
    """Parse pasted text or CSV into (row number, raw input, email candidate, name)."""
    text = text.replace("\r\n", "\n").replace("\r", "\n").lstrip("﻿")
    lines = [line for line in text.split("\n")]
    first = next((line for line in lines if line.strip()), "")
    entries: list[tuple[int, str, str, str]] = []
    header = [h.strip().lower() for h in next(csv.reader([first]))] if first else []
    if "email" in header:
        reader = csv.DictReader(io.StringIO(text))
        fieldnames = {name.strip().lower(): name for name in (reader.fieldnames or [])}
        email_key = fieldnames.get("email")
        name_key = fieldnames.get("display_name") or fieldnames.get("name") or fieldnames.get("full_name")
        for index, record in enumerate(reader, start=2):
            email = (record.get(email_key) or "") if email_key else ""
            name = (record.get(name_key) or "") if name_key else ""
            raw = ",".join(v for v in record.values() if isinstance(v, str))
            if not raw.strip():
                continue
            entries.append((index, raw[:200], email, name))
        return entries

    for index, line in enumerate(lines, start=1):
        stripped = line.strip()
        if not stripped:
            continue
        match = _ANGLE.match(stripped)
        if match:
            entries.append((index, stripped[:200], match.group("email"), match.group("name")))
            continue
        parts = [p.strip() for p in _SPLIT.split(stripped) if p.strip()]
        if len(parts) == 2 and "@" in parts[0] and "@" not in parts[1]:
            entries.append((index, stripped[:200], parts[0], parts[1]))
            continue
        tokens = [t for p in parts for t in p.split()] if len(parts) > 1 or " " in stripped else parts
        emails = [t for t in tokens if "@" in t]
        if not emails:
            entries.append((index, stripped[:200], stripped, ""))
        for token in emails:
            entries.append((index, stripped[:200], token, ""))
    return entries


async def preview_import(db: AsyncSession, programme_id: uuid.UUID, text: str) -> list[ImportRow]:
    entries = parse_import(text)
    if len(entries) > MAX_IMPORT_ROWS:
        raise validation(f"Import at most {MAX_IMPORT_ROWS} rows at a time.")
    rows: list[ImportRow] = []
    seen: dict[str, int] = {}
    for row_no, raw, email_raw, name in entries:
        try:
            stored, key = normalize_email(email_raw)
        except ValueError as exc:
            rows.append(
                ImportRow(
                    row_no, raw, email_raw.strip()[:320] or None, None, _clean_name(name), "invalid", str(exc)
                )
            )
            continue
        if key in seen:
            rows.append(
                ImportRow(
                    row_no,
                    raw,
                    stored,
                    key,
                    _clean_name(name),
                    "duplicate_in_input",
                    f"Same address as row {seen[key]}.",
                )
            )
            continue
        seen[key] = row_no
        rows.append(ImportRow(row_no, raw, stored, key, _clean_name(name), "new"))

    keys = [r.email_normalized for r in rows if r.status == "new" and r.email_normalized]
    existing: set[str] = set()
    for start in range(0, len(keys), 1000):
        chunk = keys[start : start + 1000]
        result = await db.execute(
            select(Participant.email_normalized).where(
                Participant.programme_id == programme_id, Participant.email_normalized.in_(chunk)
            )
        )
        existing.update(result.scalars())
    for row in rows:
        if row.status == "new" and row.email_normalized in existing:
            row.status = "already_imported"
            row.reason = "Already in this programme."
    return rows


async def commit_import(
    db: AsyncSession, programme_id: uuid.UUID, text: str
) -> tuple[list[ImportRow], list[uuid.UUID]]:
    """Re-validate on the server and insert only new rows. Safe against concurrent imports."""
    rows = await preview_import(db, programme_id, text)
    # Insert in one canonical order (the uniqueness key): concurrent imports of the same
    # people then lock the unique-index entries in the same order and cannot deadlock.
    pending = sorted((r for r in rows if r.status == "new"), key=lambda r: r.email_normalized or "")
    created: list[uuid.UUID] = []
    for _ in range(5):
        if not pending:
            break
        values = [
            {
                "id": uuid.uuid4(),
                "programme_id": programme_id,
                "email": r.email,
                "email_normalized": r.email_normalized,
                "display_name": r.display_name,
                "user_code": new_user_code(),
            }
            for r in pending
        ]
        stmt = (
            insert(Participant)
            .values(values)
            .on_conflict_do_nothing()
            .returning(Participant.id, Participant.email_normalized)
        )
        inserted = {row.email_normalized: row.id for row in (await db.execute(stmt)).all()}
        created.extend(inserted.values())
        missing = [r for r in pending if r.email_normalized not in inserted]
        if not missing:
            break
        # A missing row is either a concurrent import of the same address or (rarely) a
        # user-code collision. Re-check which addresses exist now and retry the rest.
        result = await db.execute(
            select(Participant.email_normalized).where(
                Participant.programme_id == programme_id,
                Participant.email_normalized.in_([r.email_normalized for r in missing]),
            )
        )
        now_existing = set(result.scalars())
        for r in missing:
            if r.email_normalized in now_existing:
                r.status = "already_imported"
                r.reason = "Imported by another request moments ago."
        pending = [r for r in missing if r.status == "new"]
    return rows, created
