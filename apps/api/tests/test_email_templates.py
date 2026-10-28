"""Organiser-edited invitation wording, previews by email and the embedded programme logo.

Every email is rendered from one of three contexts (a real invitation, a preview email
and the console preview); each is rendered here, since the templates refuse undefined
variables.
"""

from __future__ import annotations

import hashlib
import io
import re
import uuid
from typing import Any

from PIL import Image

from helpers import API, World, assert_error, drain, secret_of
from stagevote.config import get_settings
from stagevote.mailer.providers import OutgoingEmail, RecordingProvider, build_mime
from stagevote.mailer.templates import LOGO_CID, PREVIEW_LINK_TOKEN, STAGE_BANNER_CID, split_title
from stagevote.services.assets import store_generated_image
from stagevote.services.invitations import sign_in_link
from stagevote.worker.runner import Worker

TEMPLATE = "/email/templates/invitation"
WORDING: dict[str, Any] = {
    "subject": "{programme}: your voting pass",
    "heading": "See you on stage, {name}",
    "intro": "Dear {name},\r\n\r\nBring your phone. <b>bold</b> & ask {help_contact}.",
    "closing": "Cheers,\nThe crew",
    "button_label": "Start voting",
}


# Fixed under the button in every invitation; organisers cannot edit or remove it.
NOTE = (
    "This button is your personal pass. It signs you in on the first phone or computer you open it "
    "on, and again on that same device whenever you come back. On any other device it will not "
    "open, so please don't forward this email."
)
FALLBACK = "Button not working? Copy this link into the browser on the phone you will use:"


async def tick(worker: Worker) -> None:
    await worker.email_tick()
    await drain(worker)


def link_of(token: str) -> str:
    return sign_in_link(get_settings().app_base_url, token)


def tokens_in(*parts: str) -> set[str]:
    """Every sign-in link token that appears in the given text or HTML."""
    return {m for part in parts for m in re.findall(r"/enter#token=([A-Za-z0-9_-]+)", part)}


def assert_sign_in_block(text: str, html: str, link: str) -> None:
    """The fixed part of every invitation: the button with its link, the note and the
    fallback, and no user ID or password card."""
    assert f"Enter the arena: {link}" in text
    assert f'<a href="{link}"' in html and f">{link}</a>" in html
    assert NOTE in text and NOTE in html
    assert FALLBACK in html
    for part in (text, html):
        assert "User ID" not in part and "Password:" not in part and ">Password<" not in part


async def programme_row(world: World) -> Any:
    return await world.factory.fetchrow(
        "SELECT name, help_contact FROM programmes WHERE id = :p", p=world.programme_id
    )


async def invite_and_send(
    world: World, worker: Worker, provider: RecordingProvider
) -> tuple[Any, str, OutgoingEmail]:
    person = await world.participant(login=False, activated=False)
    resp = await (await world.admin()).post(f"/participants/{person.info.id}/invite")
    assert resp.status_code == 200, resp.text
    job = await world.factory.fetchrow(
        "SELECT secret_ciphertext FROM email_jobs WHERE participant_id = :p AND status = 'queued'",
        p=person.info.id,
    )
    token = secret_of(job.secret_ciphertext)
    await tick(worker)
    [message] = [m for m in provider.sent if m.to == person.info.email]
    return person, token, message


async def test_built_in_wording_is_used_until_an_organiser_saves_their_own(
    world: World, worker: Worker, provider: RecordingProvider
) -> None:
    admin = await world.admin()
    current = (await admin.get(TEMPLATE)).json()
    assert current["customised"] is False and current["wording"] == current["defaults"]
    assert {p["token"] for p in current["placeholders"]} == {
        "{name}",
        "{programme}",
        "{opens_at}",
        "{help_contact}",
    }

    assert current["defaults"]["button_label"] == "Enter the arena"

    programme = await programme_row(world)
    _, token, message = await invite_and_send(world, worker, provider)
    assert message.subject == f"Your invitation to vote at {programme.name}"
    assert "Hi Test Participant," in message.text
    assert "Tap the button below to enter. No password needed." in message.text
    assert_sign_in_block(message.text, message.html, link_of(token))
    assert ">Enter the arena</a>" in message.html
    assert tokens_in(message.text, message.html) == {token}
    assert "more than one device" not in message.text
    assert "test message" not in (message.text + message.html).lower()
    assert "Preview" not in message.subject and "example and does not work" not in message.text


async def test_saved_wording_reaches_invitations_but_never_removes_the_sign_in_link(
    world: World, worker: Worker, provider: RecordingProvider
) -> None:
    admin = await world.admin()
    resp = await admin.put(TEMPLATE, WORDING)
    assert resp.status_code == 200, resp.text
    saved = resp.json()
    assert saved["customised"] is True and saved["updated_at"] is not None
    assert saved["wording"]["intro"] == "Dear {name},\n\nBring your phone. <b>bold</b> & ask {help_contact}."

    programme = await programme_row(world)
    _, token, message = await invite_and_send(world, worker, provider)
    assert message.subject == f"{programme.name}: your voting pass"
    assert "Dear Test Participant," in message.text and "Cheers,\nThe crew" in message.text
    assert "See you on stage, Test Participant" in message.html
    assert f"ask {programme.help_contact or 'the organisers'}." in message.text
    # Organiser text is escaped, never markup.
    assert "<b>bold</b>" not in message.html and "&lt;b&gt;bold&lt;/b&gt; &amp; ask" in message.html
    # The organiser's button text labels the button; the link, the note and the text
    # part's fixed "Enter the arena:" line stay.
    assert ">Start voting</a>" in message.html and ">Enter the arena</a>" not in message.html
    assert_sign_in_block(message.text, message.html, link_of(token))

    audit = await world.factory.fetchrow(
        "SELECT count(*) AS n FROM audit_log WHERE action = 'email.template_saved' AND programme_id = :p",
        p=world.programme_id,
    )
    assert audit.n == 1

    reset = await admin.delete(TEMPLATE)
    assert reset.status_code == 200, reset.text
    assert reset.json()["customised"] is False
    assert (await admin.get(TEMPLATE)).json()["wording"] == reset.json()["defaults"]


async def test_wording_is_validated_and_nothing_is_stored_on_failure(world: World) -> None:
    admin = await world.admin()
    error = assert_error(
        await admin.put(TEMPLATE, WORDING | {"intro": "Hi {nmae}"}), 422, "VALIDATION_FAILED"
    )
    assert "{nmae}" in error["message"] and "{name}" in error["message"]
    for bad in (
        {"subject": "Line one\nBcc: someone@example.com"},
        {"heading": "tab\there"},
        {"intro": "nul \x00 byte"},
        {"intro": "   "},
        {"button_label": ""},
        {"subject": "x" * 201},
        {"html": "<script>alert(1)</script>"},
    ):
        resp = await admin.put(TEMPLATE, WORDING | bad)
        assert resp.status_code == 422, (bad, resp.text)
    assert (await admin.get(TEMPLATE)).json()["customised"] is False


async def test_preview_renders_unsaved_wording_without_storing_or_sending(world: World) -> None:
    admin = await world.admin()
    resp = await admin.post(f"{TEMPLATE}/preview", WORDING)
    assert resp.status_code == 200, resp.text
    preview = resp.json()
    programme = await programme_row(world)
    assert preview["subject"] == f"{programme.name}: your voting pass"
    assert "Dear Asha Rao," in preview["text"]
    # Only the example link, which signs nobody in (the sign-in page explains it).
    assert_sign_in_block(preview["text"], preview["html"], link_of(PREVIEW_LINK_TOKEN))
    assert tokens_in(preview["text"], preview["html"]) == {PREVIEW_LINK_TOKEN}
    assert_error(
        await world.http().post(f"{API}/auth/link", json={"token": PREVIEW_LINK_TOKEN}), 401, "LINK_INVALID"
    )
    assert (await admin.get(TEMPLATE)).json()["customised"] is False
    jobs = await world.factory.fetchrow(
        "SELECT count(*) AS n FROM email_jobs WHERE programme_id = :p", p=world.programme_id
    )
    assert jobs.n == 0


async def test_preview_email_goes_separately_to_each_address(
    world: World, worker: Worker, provider: RecordingProvider
) -> None:
    admin = await world.admin()
    tag = uuid.uuid4().hex[:8]
    raw = f"one-{tag}@example.com, Two-{tag}@Example.com;three-{tag}@example.com\nONE-{tag}@example.com"
    resp = await admin.post("/email/test", {"to": raw})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["queued"] == 3 and len(body["recipients"]) == 3
    await tick(worker)

    sent = {m.to: m for m in provider.sent if tag in m.to}
    assert set(sent) == set(body["recipients"])
    for message in sent.values():
        assert message.subject.startswith("[Preview] ")
        assert_sign_in_block(message.text, message.html, link_of(PREVIEW_LINK_TOKEN))
        assert tokens_in(message.text, message.html) == {PREVIEW_LINK_TOKEN}
        assert "example and does not work" in message.text and "example and does not work" in message.html
        assert "test message" not in (message.text + message.html).lower()
    rows = await world.factory.fetch(
        "SELECT kind, status FROM email_jobs WHERE recipient LIKE :t", t=f"%{tag}%"
    )
    assert sorted((r.kind, r.status) for r in rows) == [("test", "accepted")] * 3


async def test_preview_email_refuses_bad_or_too_many_addresses(world: World) -> None:
    admin = await world.admin()
    tag = uuid.uuid4().hex[:8]
    error = assert_error(
        await admin.post("/email/test", {"to": f"ok-{tag}@example.com, not-an-address"}),
        422,
        "VALIDATION_FAILED",
    )
    assert "not-an-address" in error["message"]
    many = ", ".join(f"p{i}-{tag}@example.com" for i in range(11))
    assert_error(await admin.post("/email/test", {"to": many}), 422, "VALIDATION_FAILED")
    assert_error(await admin.post("/email/test", {"to": " , ; "}), 422, "VALIDATION_FAILED")
    jobs = await world.factory.fetchrow(
        "SELECT count(*) AS n FROM email_jobs WHERE recipient LIKE :t", t=f"%{tag}%"
    )
    assert jobs.n == 0


async def _set_logo(world: World, storage_key: str, data: bytes = b"") -> None:
    asset_id = uuid.uuid4()
    await world.factory.execute(
        "INSERT INTO assets (id, kind, storage_key, content_type, width, height, byte_size, sha256) "
        "VALUES (:id, 'programme_logo', :k, 'image/webp', 64, 64, :n, :h)",
        id=asset_id,
        k=storage_key,
        n=len(data),
        h=hashlib.sha256(data).hexdigest(),
    )
    await world.factory.execute(
        "UPDATE programmes SET logo_asset_id = :a WHERE id = :p", a=asset_id, p=world.programme_id
    )


async def test_programme_logo_is_embedded_in_the_email_not_linked(
    world: World, worker: Worker, provider: RecordingProvider
) -> None:
    key, _, _, data = store_generated_image(Image.new("RGB", (300, 300), (200, 30, 60)), "programme_logo")
    await _set_logo(world, key, data)
    _, _, message = await invite_and_send(world, worker, provider)
    [image] = message.inline_images
    assert image.cid == LOGO_CID and image.data.startswith(b"\x89PNG")
    assert f'src="cid:{LOGO_CID}"' in message.html and "/media/" not in message.html

    mime = build_mime(get_settings(), message)
    related = [part for part in mime.walk() if part.get_content_type() == "multipart/related"]
    assert len(related) == 1
    [logo] = [part for part in mime.walk() if part.get("Content-ID") == f"<{LOGO_CID}>"]
    assert logo.get_content_type() == "image/png" and logo.get_content_disposition() == "inline"
    assert mime.get_body(preferencelist=("plain",)) is not None


async def test_a_missing_logo_file_sends_the_email_without_an_image(
    world: World, worker: Worker, provider: RecordingProvider
) -> None:
    await _set_logo(world, f"{uuid.uuid4().hex}.webp")
    _, token, message = await invite_and_send(world, worker, provider)
    assert message.inline_images == [] and "<img" not in message.html
    assert link_of(token) in message.text


async def _upload(world: World, kind: str, img: Image.Image) -> dict[str, Any]:
    admin = await world.admin()
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    resp = await admin.http.post(
        "/api/v1/admin/assets",
        data={"kind": kind},
        files={"file": ("logo.png", buf.getvalue(), "image/png")},
        headers={"X-CSRF-Token": admin.csrf},
    )
    assert resp.status_code == 201, resp.text
    body: dict[str, Any] = resp.json()
    return body


async def test_programme_logo_upload_trims_transparent_margins_and_keeps_its_shape(
    world: World, worker: Worker, provider: RecordingProvider
) -> None:
    # A 300 x 100 mark in the middle of a transparent 512 x 512 canvas.
    canvas = Image.new("RGBA", (512, 512), (0, 0, 0, 0))
    canvas.paste(Image.new("RGBA", (300, 100), (20, 40, 160, 255)), (106, 206))
    logo = await _upload(world, "programme_logo", canvas)
    assert 300 <= logo["width"] <= 316 and 100 <= logo["height"] <= 116

    # Other kinds keep their canvas (they are shown cropped to a square).
    event = await _upload(world, "event_logo", canvas)
    assert (event["width"], event["height"]) == (512, 512)

    await world.factory.execute(
        "UPDATE programmes SET logo_asset_id = :a WHERE id = :p",
        a=uuid.UUID(logo["id"]),
        p=world.programme_id,
    )
    _, _, message = await invite_and_send(world, worker, provider)
    [image] = message.inline_images
    with Image.open(io.BytesIO(image.data)) as png:
        assert png.height == 112 and 300 <= png.width <= 340
    # Displayed 56 px high at the logo's own shape, without a rounded frame.
    width = round(56 * logo["width"] / logo["height"])
    assert f'width="{width}" height="56"' in message.html
    assert "border-radius:14px" not in message.html


def test_a_trailing_year_goes_on_its_own_line() -> None:
    assert split_title("Dhanvarsha 2026") == ("Dhanvarsha", "2026")
    assert split_title("Dhanvarsha Live") == ("Dhanvarsha Live", None)
    assert split_title("Event  1999 ") == ("Event", "1999")
    assert split_title("2026") == ("2026", None)


async def test_gold_look_sends_the_night_blue_design_with_the_animated_banner(
    world: World, worker: Worker, provider: RecordingProvider
) -> None:
    await world.factory.execute(
        "UPDATE programmes SET accent = 'gold', name = 'Dhanvarsha 2026', "
        "description = 'Your voice. The spotlight.' WHERE id = :p",
        p=world.programme_id,
    )
    _, token, message = await invite_and_send(world, worker, provider)
    assert_sign_in_block(message.text, message.html, link_of(token))
    assert tokens_in(message.text, message.html) == {token}
    # The animated banner travels with the email as an inline GIF.
    [banner] = [image for image in message.inline_images if image.cid == STAGE_BANNER_CID]
    assert banner.subtype == "gif" and banner.data.startswith(b"GIF89a")
    assert f'src="cid:{STAGE_BANNER_CID}"' in message.html
    # Night blue and gold, with the year on its own line under the name.
    assert 'bgcolor="#071238"' in message.html
    assert ">Dhanvarsha</div>" in message.html and ">2026</div>" in message.html
    assert "Your voice. The spotlight." in message.html
    assert ">Enter the arena</a>" in message.html

    mime = build_mime(get_settings(), message)
    [part] = [p for p in mime.walk() if p.get("Content-ID") == f"<{STAGE_BANNER_CID}>"]
    assert part.get_content_type() == "image/gif" and part.get_content_disposition() == "inline"


async def test_console_preview_of_the_gold_look_shows_the_banner(world: World) -> None:
    await world.factory.execute("UPDATE programmes SET accent = 'gold' WHERE id = :p", p=world.programme_id)
    admin = await world.admin()
    resp = await admin.post(f"{TEMPLATE}/preview", WORDING)
    assert resp.status_code == 200, resp.text
    html = resp.json()["html"]
    assert 'src="/api/v1/public/email-banner.gif"' in html
    assert_sign_in_block(resp.json()["text"], html, link_of(PREVIEW_LINK_TOKEN))
    gif = await world.http().get(f"{API}/public/email-banner.gif")
    assert gif.status_code == 200 and gif.headers["content-type"] == "image/gif"
    assert gif.content.startswith(b"GIF89a")
