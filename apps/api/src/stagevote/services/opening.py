"""The opening showcase page: the built-in page, the organiser's saved values and their checks.

`programmes.opening` holds what the organiser saved, {} until then. Every key is optional:
a missing one comes from the built-in page, which is made from the programme's name and
description, so renaming the programme renames an unedited page too.

Two keys are about the game-show reveal rather than the page's content. "hidden" (true by
default) shows every card face down until someone scratches it open on the big screen;
false shows the cards as they are. "reveal_epoch" counts the organiser's "Hide all cards
again": every browser keeps its reveals for one epoch only, so raising it hides every card
on every screen at their next refresh. The epoch only ever grows; saving or resetting the
page keeps it.

Images are uploaded with POST /admin/assets like every other picture: the logo with kind
programme_logo, card pictures with kind event_logo. A card's button can instead show an
image or a video full screen: that file (kind opening_media) is uploaded in chunks through
/admin/media/uploads, and the card's link is then used only when it has none. The JSON
column cannot carry foreign keys, so the editor checks that each referenced asset exists
and has the right kind.
"""

from __future__ import annotations

import re
import uuid
from datetime import datetime
from typing import Any
from urllib.parse import urlsplit

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..errors import AppError, validation
from ..mailer.templates import split_title
from ..models import Asset, Programme
from . import media

# The web maps each key to a lucide-react icon; keep both lists in step.
ICON_KEYS = (
    "phone-call",
    "trophy",
    "gavel",
    "graduation-cap",
    "dices",
    "flame",
    "badge-indian-rupee",
    "hexagon",
    "fish",
    "messages-square",
    "heart-handshake",
    "sparkles",
    "award",
    "medal",
    "puzzle",
    "target",
    "crown",
    "mic",
    "film",
    "star",
    "lightbulb",
    "rocket",
    "users",
    "gift",
    "music",
    "gamepad",
)
# Shown instead of an icon a later release no longer knows.
FALLBACK_ICON = "star"

TITLE_MAX = 80
YEAR_MAX = 20
TAGLINE_MAX = 300
BUTTON_LABEL_MAX = 30
CARD_ID_MAX = 40
CARD_TITLE_MAX = 120
LINK_MAX = 500
MAX_CARDS = 40

LOGO_KIND = "programme_logo"
CARD_IMAGE_KIND = "event_logo"
CARD_MEDIA_KIND = media.KIND

DEFAULT_BUTTON_LABEL = "PLAY GAME"
DEFAULT_CARDS: tuple[tuple[str, str], ...] = (
    ("The call that changes everything", "phone-call"),
    ("Techsara placement League", "trophy"),
    ("Candidate Ki Adalat", "gavel"),
    ("Training Challenge \u2014 Choose One", "graduation-cap"),
    ("Snake and Leaders", "dices"),
    ("Roast-o-Sphere \u2014 Error 404 Not Found", "flame"),
    ("Kaun Banega Policy-Pati?", "badge-indian-rupee"),
    ("Leader\u2019s Challenge \u2014 Managers\u2019 Catan Game", "hexagon"),
    ("TechSara Shark Tank", "fish"),
    ("Owners\u2019 Conversation", "messages-square"),
    ("Employee Gratitude \u2014 Gratitude Ceremony Videos", "heart-handshake"),
    ("Because of Techsara", "sparkles"),
    ("Professional Honours", "award"),
)

# Card ids are made by the browser (a UUID) or are the built-in "default-N".
CARD_ID_PATTERN = r"^[A-Za-z0-9_-]+$"
# A link never holds whitespace, control characters or backslashes (browsers read "/\x"
# as "//x", another site).
_LINK_CHARS = re.compile(r"^[^\s\x00-\x1f\x7f\\]*$")


def link_is_safe(value: str) -> bool:
    """No link (""), an http(s) address, or a path on this site starting with one "/"."""
    if value == "":
        return True
    if not _LINK_CHARS.match(value):
        return False
    if value.startswith("/"):
        return not value.startswith("//")  # "//host" is another site
    try:
        parts = urlsplit(value)
        host = parts.hostname
    except ValueError:
        return False
    return parts.scheme.lower() in ("http", "https") and bool(host)


def _stored(programme: Programme) -> dict[str, Any]:
    return programme.opening if isinstance(programme.opening, dict) else {}


def reveal_epoch(programme: Programme) -> int:
    """The current reveal epoch: 0 until the organiser first hides the cards again."""
    value = _stored(programme).get("reveal_epoch")
    if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
        return value
    return 0


def default_page(programme: Programme) -> dict[str, Any]:
    """The built-in page: "Dhanvarsha 2026" gives the title "Dhanvarsha" and the year "2026".

    Its reveal epoch is the current one: going back to the built-in page never lowers it.
    """
    title, year = split_title(programme.name)
    return {
        "title": title,
        "year": year or "",
        "tagline": programme.description,
        "button_label": DEFAULT_BUTTON_LABEL,
        "hidden": True,
        "reveal_epoch": reveal_epoch(programme),
        "logo_asset_id": None,
        "cards": [
            {
                "id": f"default-{n}",
                "title": name,
                "icon": icon,
                "image_asset_id": None,
                "link": "",
                "media_asset_id": None,
            }
            for n, (name, icon) in enumerate(DEFAULT_CARDS, start=1)
        ],
    }


def _asset_id(value: Any) -> uuid.UUID | None:
    if not isinstance(value, str):
        return None
    try:
        return uuid.UUID(value)
    except ValueError:
        return None


def _stored_card(raw: Any) -> dict[str, Any] | None:
    """A saved card, or None when it is not one (the API never writes such a value)."""
    if not isinstance(raw, dict):
        return None
    card_id, title = raw.get("id"), raw.get("title")
    if not isinstance(card_id, str) or not card_id or not isinstance(title, str) or not title:
        return None
    icon = raw.get("icon")
    link = raw.get("link")
    return {
        "id": card_id,
        "title": title,
        "icon": icon if icon in ICON_KEYS else FALLBACK_ICON,
        "image_asset_id": _asset_id(raw.get("image_asset_id")),
        "link": link if isinstance(link, str) and link_is_safe(link) else "",
        "media_asset_id": _asset_id(raw.get("media_asset_id")),
    }


def resolve_page(programme: Programme) -> dict[str, Any]:
    """The saved values over the built-in page, key by key. Asset ids are UUIDs here."""
    page = default_page(programme)
    stored = _stored(programme)
    for key in ("title", "year", "tagline", "button_label"):
        if isinstance(stored.get(key), str):
            page[key] = stored[key]
    if isinstance(stored.get("hidden"), bool):
        page["hidden"] = stored["hidden"]
    if "logo_asset_id" in stored:
        page["logo_asset_id"] = _asset_id(stored["logo_asset_id"])
    if isinstance(stored.get("cards"), list):
        cards = [_stored_card(raw) for raw in stored["cards"]]
        page["cards"] = [card for card in cards if card is not None]
    return page


def page_assets(page: dict[str, Any]) -> list[uuid.UUID | None]:
    """The page's pictures: the logo and the card images."""
    return [page["logo_asset_id"], *(card["image_asset_id"] for card in page["cards"])]


def page_media(page: dict[str, Any]) -> list[uuid.UUID | None]:
    """The images and videos the cards' buttons show."""
    return [card["media_asset_id"] for card in page["cards"]]


async def media_assets(db: AsyncSession, ids: list[uuid.UUID | None]) -> dict[uuid.UUID, dict[str, Any]]:
    """What the editor shows of each card's image or video. The public page shows less."""
    wanted = {i for i in ids if i is not None}
    if not wanted:
        return {}
    rows = (
        await db.execute(
            select(
                Asset.id, Asset.storage_key, Asset.content_type, Asset.original_name, Asset.byte_size
            ).where(Asset.id.in_(wanted), Asset.kind == CARD_MEDIA_KIND)
        )
    ).all()
    return {
        row.id: {
            "url": f"/media/{row.storage_key}",
            "type": media.media_type(row.content_type),
            "name": row.original_name or "",
            "byte_size": row.byte_size,
        }
        for row in rows
    }


def _id_text(value: uuid.UUID | None) -> str | None:
    return str(value) if value is not None else None


def _url(urls: dict[uuid.UUID, str], value: uuid.UUID | None) -> str | None:
    return urls.get(value) if value is not None else None


def _media(files: dict[uuid.UUID, dict[str, Any]], value: uuid.UUID | None) -> dict[str, Any] | None:
    found = files.get(value) if value is not None else None
    return dict(found) if found is not None else None


def editor_view(
    page: dict[str, Any], urls: dict[uuid.UUID, str], files: dict[uuid.UUID, dict[str, Any]] | None = None
) -> dict[str, Any]:
    """The page as the organiser's editor shows it: asset ids next to their URLs, and each
    card's image or video (from media_assets) next to its id."""
    files = files or {}
    return {
        "title": page["title"],
        "year": page["year"],
        "tagline": page["tagline"],
        "button_label": page["button_label"],
        "hidden": page["hidden"],
        "reveal_epoch": page["reveal_epoch"],
        "logo_asset_id": _id_text(page["logo_asset_id"]),
        "logo_url": _url(urls, page["logo_asset_id"]),
        "cards": [
            {
                "id": card["id"],
                "title": card["title"],
                "icon": card["icon"],
                "image_asset_id": _id_text(card["image_asset_id"]),
                "image_url": _url(urls, card["image_asset_id"]),
                "link": card["link"],
                "media_asset_id": _id_text(card["media_asset_id"]),
                "media": _media(files, card["media_asset_id"]),
            }
            for card in page["cards"]
        ],
    }


def _public_media(files: dict[uuid.UUID, dict[str, Any]], value: uuid.UUID | None) -> dict[str, Any] | None:
    found = files.get(value) if value is not None else None
    return {"url": found["url"], "type": found["type"]} if found is not None else None


def public_view(
    page: dict[str, Any],
    urls: dict[uuid.UUID, str],
    updated_at: datetime,
    files: dict[uuid.UUID, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """The page as everyone sees it: URLs only, nothing private (no file names either)."""
    files = files or {}
    return {
        "title": page["title"],
        "year": page["year"],
        "tagline": page["tagline"],
        "button_label": page["button_label"],
        "hidden": page["hidden"],
        "reveal_epoch": page["reveal_epoch"],
        "logo_url": _url(urls, page["logo_asset_id"]),
        "cards": [
            {
                "id": card["id"],
                "title": card["title"],
                "icon": card["icon"],
                "image_url": _url(urls, card["image_asset_id"]),
                "link": card["link"],
                "media": _public_media(files, card["media_asset_id"]),
            }
            for card in page["cards"]
        ],
        "updated_at": updated_at,
    }


def stored_page(page: dict[str, Any], epoch: int) -> dict[str, Any]:
    """A checked page in the form kept in programmes.opening (JSON: asset ids as text).

    The reveal epoch is not part of what the editor saves: it is the one already stored.
    """
    return {
        "title": page["title"],
        "year": page["year"],
        "tagline": page["tagline"],
        "button_label": page["button_label"],
        "hidden": page["hidden"],
        "reveal_epoch": epoch,
        "logo_asset_id": _id_text(page["logo_asset_id"]),
        "cards": [
            {
                "id": card["id"],
                "title": card["title"],
                "icon": card["icon"],
                "image_asset_id": _id_text(card["image_asset_id"]),
                "link": card["link"],
                "media_asset_id": _id_text(card["media_asset_id"]),
            }
            for card in page["cards"]
        ],
    }


def reset_page(programme: Programme) -> dict[str, Any]:
    """What programmes.opening holds after "Reset to defaults": nothing but the reveal epoch."""
    epoch = reveal_epoch(programme)
    return {"reveal_epoch": epoch} if epoch else {}


def next_epoch(programme: Programme) -> dict[str, Any]:
    """programmes.opening with the reveal epoch raised by one ("Hide all cards again")."""
    return {**_stored(programme), "reveal_epoch": reveal_epoch(programme) + 1}


def _field_error(field: str, message: str) -> AppError:
    # The same details shape as request validation errors, so the editor can mark the field.
    return validation(message, {"fields": [{"field": field, "message": message}]})


def check_card_ids(page: dict[str, Any]) -> None:
    seen: set[str] = set()
    for index, card in enumerate(page["cards"]):
        if card["id"] in seen:
            raise _field_error(f"cards.{index}.id", f"Card {index + 1} has the same id as another card.")
        seen.add(card["id"])


async def check_assets(db: AsyncSession, page: dict[str, Any]) -> None:
    """Every referenced image or video exists and was uploaded for its place on the page."""
    wanted = {i for i in [*page_assets(page), *page_media(page)] if i is not None}
    if not wanted:
        return
    kinds = {
        row.id: row.kind
        for row in (await db.execute(select(Asset.id, Asset.kind).where(Asset.id.in_(wanted)))).all()
    }
    logo = page["logo_asset_id"]
    if logo is not None:
        if logo not in kinds:
            raise _field_error("logo_asset_id", "The logo image was not found. Upload it again.")
        if kinds[logo] != LOGO_KIND:
            raise _field_error(
                "logo_asset_id", "That image was not uploaded as a logo. Upload the logo again."
            )
    for index, card in enumerate(page["cards"]):
        image = card["image_asset_id"]
        if image is None:
            continue
        field = f"cards.{index}.image_asset_id"
        if image not in kinds:
            raise _field_error(field, f"The picture of card {index + 1} was not found. Upload it again.")
        if kinds[image] != CARD_IMAGE_KIND:
            raise _field_error(
                field,
                f"The picture of card {index + 1} was not uploaded as a card picture. Upload it again.",
            )
    for index, card in enumerate(page["cards"]):
        shown = card["media_asset_id"]
        if shown is None:
            continue
        field = f"cards.{index}.media_asset_id"
        if shown not in kinds:
            raise _field_error(
                field, f"The image or video of card {index + 1} was not found. Upload it again."
            )
        if kinds[shown] != CARD_MEDIA_KIND:
            raise _field_error(
                field,
                f"The image or video of card {index + 1} was not uploaded for its button. "
                'Upload it with "Upload image or video".',
            )
