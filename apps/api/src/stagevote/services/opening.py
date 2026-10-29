"""The opening showcase page: the built-in page, the organiser's saved values and their checks.

`programmes.opening` holds what the organiser saved, {} until then. Every key is optional:
a missing one comes from the built-in page, which is made from the programme's name and
description, so renaming the programme renames an unedited page too.

Images are uploaded with POST /admin/assets like every other picture: the logo with kind
programme_logo, card pictures with kind event_logo. The JSON column cannot carry foreign
keys, so the editor checks that each referenced asset exists and has the right kind.
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


def default_page(programme: Programme) -> dict[str, Any]:
    """The built-in page: "Dhanvarsha 2026" gives the title "Dhanvarsha" and the year "2026"."""
    title, year = split_title(programme.name)
    return {
        "title": title,
        "year": year or "",
        "tagline": programme.description,
        "button_label": DEFAULT_BUTTON_LABEL,
        "logo_asset_id": None,
        "cards": [
            {"id": f"default-{n}", "title": name, "icon": icon, "image_asset_id": None, "link": ""}
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
    }


def resolve_page(programme: Programme) -> dict[str, Any]:
    """The saved values over the built-in page, key by key. Asset ids are UUIDs here."""
    page = default_page(programme)
    stored = programme.opening if isinstance(programme.opening, dict) else {}
    for key in ("title", "year", "tagline", "button_label"):
        if isinstance(stored.get(key), str):
            page[key] = stored[key]
    if "logo_asset_id" in stored:
        page["logo_asset_id"] = _asset_id(stored["logo_asset_id"])
    if isinstance(stored.get("cards"), list):
        cards = [_stored_card(raw) for raw in stored["cards"]]
        page["cards"] = [card for card in cards if card is not None]
    return page


def page_assets(page: dict[str, Any]) -> list[uuid.UUID | None]:
    return [page["logo_asset_id"], *(card["image_asset_id"] for card in page["cards"])]


def _id_text(value: uuid.UUID | None) -> str | None:
    return str(value) if value is not None else None


def _url(urls: dict[uuid.UUID, str], value: uuid.UUID | None) -> str | None:
    return urls.get(value) if value is not None else None


def editor_view(page: dict[str, Any], urls: dict[uuid.UUID, str]) -> dict[str, Any]:
    """The page as the organiser's editor shows it: asset ids next to their URLs."""
    return {
        "title": page["title"],
        "year": page["year"],
        "tagline": page["tagline"],
        "button_label": page["button_label"],
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
            }
            for card in page["cards"]
        ],
    }


def public_view(page: dict[str, Any], urls: dict[uuid.UUID, str], updated_at: datetime) -> dict[str, Any]:
    """The page as everyone sees it: URLs only, nothing private."""
    return {
        "title": page["title"],
        "year": page["year"],
        "tagline": page["tagline"],
        "button_label": page["button_label"],
        "logo_url": _url(urls, page["logo_asset_id"]),
        "cards": [
            {
                "id": card["id"],
                "title": card["title"],
                "icon": card["icon"],
                "image_url": _url(urls, card["image_asset_id"]),
                "link": card["link"],
            }
            for card in page["cards"]
        ],
        "updated_at": updated_at,
    }


def stored_page(page: dict[str, Any]) -> dict[str, Any]:
    """A checked page in the form kept in programmes.opening (JSON: asset ids as text)."""
    return {
        "title": page["title"],
        "year": page["year"],
        "tagline": page["tagline"],
        "button_label": page["button_label"],
        "logo_asset_id": _id_text(page["logo_asset_id"]),
        "cards": [
            {
                "id": card["id"],
                "title": card["title"],
                "icon": card["icon"],
                "image_asset_id": _id_text(card["image_asset_id"]),
                "link": card["link"],
            }
            for card in page["cards"]
        ],
    }


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
    """Every referenced image exists and was uploaded for its place on the page."""
    wanted = {i for i in page_assets(page) if i is not None}
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
