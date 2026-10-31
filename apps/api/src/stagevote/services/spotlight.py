"""The spotlight screen (/spotlight): people revealed one at a time on the big screen.

The organiser enters people in the console (a name, an optional line under it such as an
award, and a picture) and presses "Show" for one of them: on every screen showing /spotlight
that person's face-down card flies to the middle, scratches itself open, shows the picture and
name, and goes back to its place, face up.

`programmes.spotlight` holds, as one JSON object:

* what the editor saves: "version" (each save raises it, so two organisers cannot overwrite
  each other; it is the spotlight's own, so it never makes the programme settings or the
  opening page stale), "title", "subtitle", "hold_seconds" (how long a card stays in the
  middle; 0 keeps it there until the organiser sends it back or shows someone else) and
  "entries", each {"id", "name", "detail", "image_asset_id"};
* what the live buttons change: "revealed" (ids shown since the last reset, in that order),
  "seq" (counts Show commands), "shown" ({"entry_id", "seq", "back", "first"}: the latest
  Show; "back" once the organiser sent the card back, "first" when that Show revealed the
  card) and "epoch" (raised by "Hide all again").

Screens poll the public view and play each Show once, by its seq. The public view names only
revealed people: a face-down card is an id and nothing more, so the page never gives the
next name away. A missing or malformed stored value falls back to its default, so a
hand-edited row never breaks the screen.
"""

from __future__ import annotations

import re
import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..errors import AppError, validation
from ..models import Asset, Programme
from . import media

DEFAULT_TITLE = "Spotlight"
DEFAULT_HOLD_SECONDS = 10
TITLE_MAX = 80
SUBTITLE_MAX = 160
NAME_MAX = 80
DETAIL_MAX = 120
HOLD_MAX = 600
MAX_ENTRIES = 60
ENTRY_ID_MAX = 40
# Entry ids are made by the browser (a UUID, or 32 hex digits without a secure context).
ENTRY_ID_PATTERN = r"^[A-Za-z0-9_-]+$"
_ENTRY_ID = re.compile(ENTRY_ID_PATTERN)
# Pictures go up in chunks like the opening page's full-screen media, so a photo straight
# from a phone (or a print file) needs no resizing first. Only images are accepted here.
IMAGE_KIND = media.KIND


def _stored(programme: Programme) -> dict[str, Any]:
    value = programme.spotlight
    return value if isinstance(value, dict) else {}


def _count(value: Any, default: int = 0, high: int | None = None) -> int:
    """A non-negative whole number (not a bool), else the default."""
    if (
        isinstance(value, int)
        and not isinstance(value, bool)
        and value >= 0
        and (high is None or value <= high)
    ):
        return value
    return default


def _text(value: Any, default: str, limit: int) -> str:
    return value if isinstance(value, str) and len(value) <= limit else default


def _asset_id(value: Any) -> uuid.UUID | None:
    if not isinstance(value, str):
        return None
    try:
        return uuid.UUID(value)
    except ValueError:
        return None


def _entry(raw: Any) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None
    entry_id, name = raw.get("id"), raw.get("name")
    if not isinstance(entry_id, str) or not _ENTRY_ID.match(entry_id) or len(entry_id) > ENTRY_ID_MAX:
        return None
    if not isinstance(name, str) or not name or len(name) > NAME_MAX:
        return None
    return {
        "id": entry_id,
        "name": name,
        "detail": _text(raw.get("detail"), "", DETAIL_MAX),
        "image_asset_id": _asset_id(raw.get("image_asset_id")),
    }


def resolve(programme: Programme) -> dict[str, Any]:
    """The spotlight as stored, with defaults for anything missing or malformed."""
    stored = _stored(programme)
    entries: list[dict[str, Any]] = []
    seen: set[str] = set()
    raw_entries = stored.get("entries")
    for raw in raw_entries if isinstance(raw_entries, list) else []:
        entry = _entry(raw)
        if entry is not None and entry["id"] not in seen:
            seen.add(entry["id"])
            entries.append(entry)
    revealed: list[str] = []
    raw_revealed = stored.get("revealed")
    for value in raw_revealed if isinstance(raw_revealed, list) else []:
        if isinstance(value, str) and value in seen and value not in revealed:
            revealed.append(value)
    seq = _count(stored.get("seq"))
    shown: dict[str, Any] | None = None
    raw_shown = stored.get("shown")
    if isinstance(raw_shown, dict) and raw_shown.get("entry_id") in seen:
        shown = {
            "entry_id": raw_shown["entry_id"],
            "seq": _count(raw_shown.get("seq"), seq),
            "back": raw_shown.get("back") is True,
            "first": raw_shown.get("first") is True,
        }
    title = _text(stored.get("title"), DEFAULT_TITLE, TITLE_MAX)
    return {
        "version": _count(stored.get("version")),
        "title": title or DEFAULT_TITLE,
        "subtitle": _text(stored.get("subtitle"), "", SUBTITLE_MAX),
        "hold_seconds": _count(stored.get("hold_seconds"), DEFAULT_HOLD_SECONDS, HOLD_MAX),
        "entries": entries,
        "revealed": revealed,
        "seq": seq,
        "shown": shown,
        "epoch": _count(stored.get("epoch")),
    }


def stored(state: dict[str, Any]) -> dict[str, Any]:
    """A resolved spotlight in the form kept in programmes.spotlight (asset ids as text)."""
    return {
        "version": state["version"],
        "title": state["title"],
        "subtitle": state["subtitle"],
        "hold_seconds": state["hold_seconds"],
        "entries": [
            {
                "id": entry["id"],
                "name": entry["name"],
                "detail": entry["detail"],
                "image_asset_id": str(entry["image_asset_id"]) if entry["image_asset_id"] else None,
            }
            for entry in state["entries"]
        ],
        "revealed": list(state["revealed"]),
        "seq": state["seq"],
        "shown": dict(state["shown"]) if state["shown"] else None,
        "epoch": state["epoch"],
    }


# ---- changes ------------------------------------------------------------------------


def saved(state: dict[str, Any], content: dict[str, Any]) -> dict[str, Any]:
    """The editor's save: new title, subtitle, hold and people; the live state carries on for
    the people still there."""
    entries = [
        {
            "id": entry["id"],
            "name": entry["name"],
            "detail": entry["detail"],
            "image_asset_id": entry["image_asset_id"],
        }
        for entry in content["entries"]
    ]
    ids = {entry["id"] for entry in entries}
    shown = state["shown"] if state["shown"] and state["shown"]["entry_id"] in ids else None
    return {
        **state,
        "version": state["version"] + 1,
        "title": content["title"],
        "subtitle": content["subtitle"],
        "hold_seconds": content["hold_seconds"],
        "entries": entries,
        "revealed": [entry_id for entry_id in state["revealed"] if entry_id in ids],
        "shown": shown,
    }


def showing(state: dict[str, Any], entry_id: str) -> dict[str, Any]:
    """Show one person: their card comes to the middle on every screen, revealed."""
    seq = state["seq"] + 1
    first = entry_id not in state["revealed"]
    revealed = [*state["revealed"], entry_id] if first else list(state["revealed"])
    return {
        **state,
        "seq": seq,
        "revealed": revealed,
        "shown": {"entry_id": entry_id, "seq": seq, "back": False, "first": first},
    }


def sent_back(state: dict[str, Any]) -> dict[str, Any]:
    """The card in the middle goes back to its place now."""
    if state["shown"] is None or state["shown"]["back"]:
        return state
    return {**state, "shown": {**state["shown"], "back": True}}


def hidden_again(state: dict[str, Any]) -> dict[str, Any]:
    """Every card face down again, nothing in the middle."""
    return {**state, "revealed": [], "shown": None, "epoch": state["epoch"] + 1}


# ---- checks -------------------------------------------------------------------------


def field_error(field: str, message: str) -> AppError:
    # The same details shape as request validation errors, so the editor can mark the field.
    return validation(message, {"fields": [{"field": field, "message": message}]})


def check_entry_ids(entries: list[dict[str, Any]]) -> None:
    seen: set[str] = set()
    for index, entry in enumerate(entries):
        if entry["id"] in seen:
            raise field_error(f"entries.{index}.id", f"Person {index + 1} has the same id as another person.")
        seen.add(entry["id"])


async def check_images(db: AsyncSession, entries: list[dict[str, Any]]) -> None:
    """Every picture exists, was uploaded through the chunked uploader and is an image."""
    wanted = {entry["image_asset_id"] for entry in entries if entry["image_asset_id"] is not None}
    if not wanted:
        return
    found = {
        row.id: (row.kind, row.content_type)
        for row in (
            await db.execute(select(Asset.id, Asset.kind, Asset.content_type).where(Asset.id.in_(wanted)))
        ).all()
    }
    for index, entry in enumerate(entries):
        image = entry["image_asset_id"]
        if image is None:
            continue
        field = f"entries.{index}.image_asset_id"
        if image not in found:
            raise field_error(field, f"The picture of person {index + 1} was not found. Upload it again.")
        kind, content_type = found[image]
        if kind != IMAGE_KIND or media.media_type(content_type) != "image":
            raise field_error(field, f"The file of person {index + 1} is not a picture. Upload a picture.")


# ---- views --------------------------------------------------------------------------


async def images(db: AsyncSession, state: dict[str, Any]) -> dict[uuid.UUID, dict[str, Any]]:
    """The people's pictures: URL, size and the uploaded file's name."""
    wanted = {entry["image_asset_id"] for entry in state["entries"] if entry["image_asset_id"] is not None}
    if not wanted:
        return {}
    rows = (
        await db.execute(
            select(
                Asset.id,
                Asset.storage_key,
                Asset.content_type,
                Asset.original_name,
                Asset.byte_size,
                Asset.width,
                Asset.height,
            ).where(Asset.id.in_(wanted), Asset.kind == IMAGE_KIND)
        )
    ).all()
    return {
        row.id: {
            "url": f"/media/{row.storage_key}",
            "name": row.original_name or "",
            "byte_size": row.byte_size,
            "width": row.width,
            "height": row.height,
        }
        for row in rows
        if media.media_type(row.content_type) == "image"
    }


def _shown(state: dict[str, Any]) -> dict[str, Any] | None:
    shown = state["shown"]
    if shown is None:
        return None
    return {"id": shown["entry_id"], "seq": shown["seq"], "back": shown["back"], "first": shown["first"]}


def editor_view(state: dict[str, Any], files: dict[uuid.UUID, dict[str, Any]]) -> dict[str, Any]:
    """Everything, for the organiser: each picture's id next to what the editor shows of it."""

    def image(entry: dict[str, Any]) -> dict[str, Any] | None:
        found = files.get(entry["image_asset_id"]) if entry["image_asset_id"] else None
        return dict(found) if found else None

    return {
        "version": state["version"],
        "title": state["title"],
        "subtitle": state["subtitle"],
        "hold_seconds": state["hold_seconds"],
        "entries": [
            {
                "id": entry["id"],
                "name": entry["name"],
                "detail": entry["detail"],
                "image_asset_id": str(entry["image_asset_id"]) if entry["image_asset_id"] else None,
                "image": image(entry),
            }
            for entry in state["entries"]
        ],
        "revealed": list(state["revealed"]),
        "seq": state["seq"],
        "shown": _shown(state),
        "epoch": state["epoch"],
    }


def public_view(
    state: dict[str, Any], files: dict[uuid.UUID, dict[str, Any]], logo_url: str | None
) -> dict[str, Any]:
    """The screen's view: one card per person in order; only revealed cards carry a name."""
    revealed = set(state["revealed"])
    cards: list[dict[str, Any]] = []
    for entry in state["entries"]:
        if entry["id"] not in revealed:
            cards.append({"id": entry["id"], "revealed": False})
            continue
        found = files.get(entry["image_asset_id"]) if entry["image_asset_id"] else None
        cards.append(
            {
                "id": entry["id"],
                "revealed": True,
                "name": entry["name"],
                "detail": entry["detail"],
                "image_url": found["url"] if found else None,
            }
        )
    return {
        "title": state["title"],
        "subtitle": state["subtitle"],
        "logo_url": logo_url,
        "hold_seconds": state["hold_seconds"],
        "epoch": state["epoch"],
        "seq": state["seq"],
        "shown": _shown(state),
        "cards": cards,
    }
