"""Render branded HTML and plain-text emails.

Organisers can edit the invitation's wording (subject, heading, opening message, closing
note and button text). Their text is never treated as a template: it is HTML-escaped and
only the placeholders in PLACEHOLDERS are substituted, so it cannot inject markup or reach
Jinja. The button's personal sign-in link, the note about that link and the footer always
come from the fixed template, so an edit cannot remove them.
"""

from __future__ import annotations

import io
import re
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from jinja2 import Environment, FileSystemLoader, StrictUndefined, select_autoescape
from markupsafe import Markup, escape
from PIL import Image

_DIR = Path(__file__).resolve().parent.parent / "templates" / "email"
_html = Environment(
    loader=FileSystemLoader(_DIR), autoescape=select_autoescape(["html"]), undefined=StrictUndefined
)
_text = Environment(loader=FileSystemLoader(_DIR), autoescape=False, undefined=StrictUndefined)  # noqa: S701 - plain text

ACCENT_HEX = {
    "violet": "#5b34c4",
    "plum": "#7a2a6e",
    "indigo": "#3b43b8",
    "teal": "#136f6a",
    "rose": "#b02a55",
    "amber": "#8a5a00",
    # The gold look: white text on gold is unreadable in email, so emails use its royal blue.
    "gold": "#23339a",
}

PLACEHOLDERS: dict[str, str] = {
    "name": "the participant's name, or “there” when no name is known",
    "programme": "the programme name",
    "opens_at": "when sign-in opens",
    "help_contact": "the help contact from the programme settings",
}
_TOKEN = re.compile(r"\{([^{}\s]{1,40})\}")

# Previews link to /enter#token=example instead of a personal link. It is not a token at
# all, so it never signs anyone in; the sign-in page says it comes from a preview email.
PREVIEW_LINK_TOKEN = "example"  # noqa: S105 - a visible example, not a credential
LOGO_CID = "programme-logo@stagevote"
# The gold look's animated banner: night sky, the ticket emblem, falling coins and twinkling
# stars (made by scripts/make-email-banner.py). Mail apps do not run CSS animation, so the
# motion is a GIF; Outlook on Windows shows its first frame, which is a full picture.
STAGE_BANNER_CID = "stage-banner@stagevote"
# The TechSara mark in the gold look's footer, as on the sign-in page.
BRAND_MARK_CID = "brand-mark@stagevote"
_YEAR = re.compile(r"^(.*\S)\s+((?:19|20)\d{2})$")
# The logo keeps its own shape: this height, and a width up to LOGO_MAX_WIDTH.
LOGO_HEIGHT = 56
LOGO_MAX_WIDTH = 200


@dataclass(frozen=True)
class Wording:
    subject: str
    heading: str
    intro: str
    closing: str
    button_label: str

    def as_dict(self) -> dict[str, str]:
        return asdict(self)


DEFAULT_INVITATION = Wording(
    subject="Your invitation to vote at {programme}",
    heading="You're invited to vote",
    intro=(
        "Hi {name},\n\n"
        "You're invited to vote at {programme}. Tap the button below to enter. No password needed."
    ),
    closing=(
        "Keep this email: the button signs you in, and it works again on the same phone whenever "
        "you come back.\n\n"
        "If you change phone or get stuck, ask {help_contact} to send you a new invitation. The old "
        "link then stops working."
    ),
    button_label="Enter the arena",
)


def format_local(value: datetime | None, tz: str) -> str | None:
    if value is None:
        return None
    local = value.astimezone(ZoneInfo(tz))
    return local.strftime("%a %d %b %Y, %H:%M ") + (local.tzname() or tz)


def unknown_placeholders(*texts: str) -> list[str]:
    found: list[str] = []
    for value in texts:
        for match in _TOKEN.finditer(value):
            if match.group(1) not in PLACEHOLDERS and match.group(0) not in found:
                found.append(match.group(0))
    return found


def _fill(value: str, values: dict[str, str]) -> str:
    # One pass over the organiser's text: substituted values are never scanned again.
    return _TOKEN.sub(lambda m: values.get(m.group(1), m.group(0)), value)


def _paragraphs(value: str) -> list[str]:
    return [p.strip() for p in re.split(r"\n[ \t]*\n", value.strip()) if p.strip()]


def _text_block(value: str, values: dict[str, str]) -> str:
    return "\n\n".join(_fill(p, values) for p in _paragraphs(value))


def _html_block(value: str, values: dict[str, str]) -> Markup:
    # Placeholders are filled first and the result is escaped as a whole, so neither the
    # organiser's text nor a participant's name can add markup.
    paragraph = Markup('<p style="margin:0 0 14px 0;">{}</p>')
    return Markup("").join(
        paragraph.format(Markup("<br>").join(escape(line) for line in _fill(p, values).split("\n")))
        for p in _paragraphs(value)
    )


def _single_line(value: str) -> str:
    return " ".join(value.split())


def render_invitation(
    context: dict[str, Any], wording: Wording, *, preview: bool = False
) -> tuple[str, str, str]:
    """Return (subject, text, html) for an invitation, or for a preview of one."""
    values = {
        "name": _single_line(context.get("display_name") or "") or "there",
        "programme": context["programme_name"],
        "opens_at": context.get("opens_at") or "soon",
        "help_contact": context.get("help_contact") or "the organisers",
    }
    subject = _single_line(_fill(wording.subject, values))
    if preview:
        subject = f"[Preview] {subject}"
    heading = _single_line(_fill(wording.heading, values))
    full = {
        **context,
        "subject": subject,
        "heading": heading,
        "preheader": _single_line(_text_block(wording.intro, values))[:140],
        "intro_text": _text_block(wording.intro, values),
        "intro_html": _html_block(wording.intro, values),
        "closing_text": _text_block(wording.closing, values),
        "closing_html": _html_block(wording.closing, values),
        "button_label": _single_line(_fill(wording.button_label, values)),
        "preview": preview,
    }
    full.setdefault("logo_src", None)
    full.setdefault("banner_src", None)
    full.setdefault("mark_src", None)
    text = _text.get_template("invitation.txt").render(full)
    # The gold look has its own night-blue design; every other accent uses the light one.
    page = "invitation_stage.html" if full.get("theme") == "stage" else "invitation.html"
    html = _html.get_template(page).render(full)
    return subject, text, html


def split_title(name: str) -> tuple[str, str | None]:
    """'Dhanvarsha 2026' -> ('Dhanvarsha', '2026'): the year is shown on its own line."""
    match = _YEAR.match(name.strip())
    return (match.group(1), match.group(2)) if match else (name, None)


_banner: bytes | None = None


def stage_banner_gif() -> bytes:
    global _banner
    if _banner is None:
        _banner = (_DIR / "stage-banner.gif").read_bytes()
    return _banner


_mark: bytes | None = None


def brand_mark_png() -> bytes:
    global _mark
    if _mark is None:
        _mark = (_DIR / "brand-mark.png").read_bytes()
    return _mark


_logo_cache: dict[str, bytes] = {}


def email_logo_png(media_root: str, storage_key: str) -> bytes | None:
    """The programme logo as a PNG at twice its display size, for embedding in emails
    (most mail apps cannot show WebP, and a link to the site's /media fails when the site
    is not public)."""
    if Path(storage_key).name != storage_key:
        return None
    cached = _logo_cache.get(storage_key)
    if cached is not None:
        return cached
    try:
        with Image.open(Path(media_root) / storage_key) as source:
            img = source.convert("RGBA")
            img.thumbnail((LOGO_MAX_WIDTH * 2, LOGO_HEIGHT * 2), Image.Resampling.LANCZOS)
            out = io.BytesIO()
            img.save(out, format="PNG", optimize=True)
    except (OSError, ValueError, Image.DecompressionBombError):
        return None
    data = out.getvalue()
    if len(_logo_cache) >= 16:
        _logo_cache.clear()
    _logo_cache[storage_key] = data
    return data
