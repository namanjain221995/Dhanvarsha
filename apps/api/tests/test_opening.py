"""The opening showcase page: the built-in page, the organiser's editor and the public page.

The public page needs no sign-in and shows only titles, links and image URLs. The editor
saves the whole page at once and shares the programme's settings_version, so two
organisers (or this page and the programme settings form) cannot overwrite each other.
"""

from __future__ import annotations

import copy
import io
import json
import uuid
from typing import Any

import pytest
from PIL import Image

from helpers import ADMIN_API, API, AdminClient, World, assert_error
from stagevote.services.opening import DEFAULT_CARDS, ICON_KEYS

DEFAULT_TITLES = [title for title, _ in DEFAULT_CARDS]
DEFAULT_ICONS = [icon for _, icon in DEFAULT_CARDS]


async def _name(world: World, name: str, description: str = "") -> None:
    await world.factory.execute(
        "UPDATE programmes SET name = :n, description = :d WHERE id = :p",
        n=name,
        d=description,
        p=world.programme_id,
    )


async def _public(world: World) -> dict[str, Any]:
    resp = await world.http(origin=None).get(f"{API}/public/opening")
    assert resp.status_code == 200, resp.text
    assert resp.headers["cache-control"].startswith("no-store")
    body: dict[str, Any] = resp.json()
    return body


async def _editor(admin: AdminClient) -> dict[str, Any]:
    resp = await admin.get("/opening")
    assert resp.status_code == 200, resp.text
    body: dict[str, Any] = resp.json()
    return body


def _body(editor: dict[str, Any], **changes: Any) -> dict[str, Any]:
    """A save request made from what the editor loaded, as the console sends it."""
    page = copy.deepcopy(editor["opening"])
    body: dict[str, Any] = {
        "settings_version": editor["settings_version"],
        "title": page["title"],
        "year": page["year"],
        "tagline": page["tagline"],
        "button_label": page["button_label"],
        "logo_asset_id": page["logo_asset_id"],
        "cards": [
            {k: card[k] for k in ("id", "title", "icon", "image_asset_id", "link")} for card in page["cards"]
        ],
    }
    body.update(changes)
    return body


async def _upload(admin: AdminClient, kind: str) -> str:
    buf = io.BytesIO()
    Image.new("RGB", (64, 64), (212, 175, 55)).save(buf, format="PNG")
    resp = await admin.http.post(
        f"{ADMIN_API}/assets",
        data={"kind": kind},
        files={"file": ("picture.png", buf.getvalue(), "image/png")},
        headers={"X-CSRF-Token": admin.csrf},
    )
    assert resp.status_code == 201, resp.text
    return str(resp.json()["id"])


def _fields(resp: Any) -> list[str]:
    return [f["field"] for f in resp.json()["error"]["details"]["fields"]]


async def test_built_in_page_comes_from_the_programme_name_and_description(world: World) -> None:
    await _name(world, "Dhanvarsha 2026", "An evening of games, honours and gold.")
    public = await _public(world)
    assert set(public) == {"title", "year", "tagline", "button_label", "logo_url", "cards", "updated_at"}
    assert (public["title"], public["year"], public["tagline"], public["button_label"]) == (
        "Dhanvarsha",
        "2026",
        "An evening of games, honours and gold.",
        "PLAY GAME",
    )
    assert public["logo_url"] is None and public["updated_at"]
    assert [c["id"] for c in public["cards"]] == [f"default-{n}" for n in range(1, 14)]
    assert [c["title"] for c in public["cards"]] == DEFAULT_TITLES
    assert [c["icon"] for c in public["cards"]] == DEFAULT_ICONS
    assert public["cards"][7]["title"] == "Leader\u2019s Challenge \u2014 Managers\u2019 Catan Game"
    assert all(c["image_url"] is None and c["link"] == "" for c in public["cards"])
    assert set(public["cards"][0]) == {"id", "title", "icon", "image_url", "link"}

    admin = await world.admin()
    editor = await _editor(admin)
    assert set(editor) == {"opening", "defaults", "icons", "settings_version"}
    assert editor["opening"] == editor["defaults"]
    assert editor["icons"] == list(ICON_KEYS)
    assert editor["settings_version"] == await admin.settings_version()
    assert (editor["opening"]["logo_asset_id"], editor["opening"]["logo_url"]) == (None, None)
    assert editor["opening"]["cards"][0] == {
        "id": "default-1",
        "title": "The call that changes everything",
        "icon": "phone-call",
        "image_asset_id": None,
        "image_url": None,
        "link": "",
    }

    # An unedited page follows the programme: a name without a year has no year line.
    await _name(world, "Test Showcase", "")
    public = await _public(world)
    assert (public["title"], public["year"], public["tagline"]) == ("Test Showcase", "", "")


async def test_saved_page_is_public_and_keeps_card_order(world: World) -> None:
    await _name(world, "Dhanvarsha 2026")
    admin = await world.admin()
    editor = await _editor(admin)
    cards = editor["opening"]["cards"]
    # Move card 3 to the top, rename card 1, drop the last card and add a new one.
    cards.insert(0, cards.pop(2))
    cards[1]["title"] = "The call that changes it all"
    cards.pop()
    new_id = str(uuid.uuid4())
    cards.append(
        {"id": new_id, "title": "Grand Finale", "icon": "crown", "image_asset_id": None, "link": "/vote"}
    )
    body = _body(
        editor,
        title="Dhanvarsha Live",
        year="Season 2",
        tagline="  Games, honours\r\nand gold.  ",
        button_label="LET'S PLAY",
        cards=[{k: c[k] for k in ("id", "title", "icon", "image_asset_id", "link")} for c in cards],
    )
    resp = await admin.put("/opening", body)
    assert resp.status_code == 200, resp.text
    saved = resp.json()
    assert saved["settings_version"] == editor["settings_version"] + 1
    assert saved == await _editor(admin)
    page = saved["opening"]
    assert (page["title"], page["year"], page["tagline"], page["button_label"]) == (
        "Dhanvarsha Live",
        "Season 2",
        "Games, honours\nand gold.",
        "LET'S PLAY",
    )
    expected_ids = ["default-3", "default-1", *(f"default-{n}" for n in range(2, 13) if n != 3), new_id]
    assert [c["id"] for c in page["cards"]] == expected_ids
    assert saved["defaults"]["cards"][0]["id"] == "default-1"  # the built-in page is unchanged

    public = await _public(world)
    assert (public["title"], public["year"], public["button_label"]) == (
        "Dhanvarsha Live",
        "Season 2",
        "LET'S PLAY",
    )
    assert [c["id"] for c in public["cards"]] == expected_ids
    assert public["cards"][1]["title"] == "The call that changes it all"
    assert public["cards"][-1] == {
        "id": new_id,
        "title": "Grand Finale",
        "icon": "crown",
        "image_url": None,
        "link": "/vote",
    }

    # Saved values no longer follow the programme name; the built-in page still does.
    await _name(world, "Renamed 2027")
    again = await _editor(admin)
    assert again["opening"]["title"] == "Dhanvarsha Live"
    assert (again["defaults"]["title"], again["defaults"]["year"]) == ("Renamed", "2027")

    # Every card can be removed: the page then shows its header alone.
    resp = await admin.put("/opening", _body(again, cards=[]))
    assert resp.status_code == 200, resp.text
    assert (await _public(world))["cards"] == []

    [entry, _] = await world.factory.fetch(
        "SELECT action, details FROM audit_log WHERE programme_id = :p AND action LIKE 'opening.%' "
        "ORDER BY id DESC",
        p=world.programme_id,
    )
    assert (entry.action, entry.details) == ("opening.updated", {"cards": 0})


async def test_icons_are_checked(world: World) -> None:
    admin = await world.admin()
    editor = await _editor(admin)
    body = _body(editor)
    body["cards"][4]["icon"] = "skull"
    resp = await admin.put("/opening", body)
    assert_error(resp, 422, "VALIDATION_FAILED")
    assert _fields(resp) == ["cards.4.icon"]
    assert "listed icons" in resp.json()["error"]["details"]["fields"][0]["message"]

    for icon in ICON_KEYS:
        body["cards"][4]["icon"] = icon
        resp = await admin.put("/opening", body)
        assert resp.status_code == 200, resp.text
        body["settings_version"] = resp.json()["settings_version"]
    assert (await _public(world))["cards"][4]["icon"] == ICON_KEYS[-1]


@pytest.mark.parametrize(
    ("link", "stored"),
    [
        ("", ""),
        ("https://example.com/games/1?round=2#top", "https://example.com/games/1?round=2#top"),
        ("HTTP://example.com", "HTTP://example.com"),
        ("  /vote  ", "/vote"),
        ("/admin/opening", "/admin/opening"),
    ],
)
async def test_links_that_are_accepted(world: World, link: str, stored: str) -> None:
    admin = await world.admin()
    body = _body(await _editor(admin))
    body["cards"][0]["link"] = link
    resp = await admin.put("/opening", body)
    assert resp.status_code == 200, resp.text
    assert (await _public(world))["cards"][0]["link"] == stored


@pytest.mark.parametrize(
    "link",
    [
        "javascript:alert(1)",
        "data:text/html,<b>hi</b>",
        "//evil.example/path",
        "/\\evil.example",
        "ftp://example.com/file",
        "https://",
        "example.com",
        "vote",
        "https://exa mple.com",
        "/line\nbreak",
        "https://[::1",
        "https://example.com/" + "a" * 500,
    ],
)
async def test_links_that_are_refused(world: World, link: str) -> None:
    admin = await world.admin()
    body = _body(await _editor(admin))
    body["cards"][2]["link"] = link
    resp = await admin.put("/opening", body)
    assert_error(resp, 422, "VALIDATION_FAILED")
    assert _fields(resp) == ["cards.2.link"]


async def test_page_fields_are_checked(world: World) -> None:
    admin = await world.admin()
    editor = await _editor(admin)
    cards = _body(editor)["cards"]
    too_many = [
        {"id": f"card-{n}", "title": f"Card {n}", "icon": "star", "image_asset_id": None, "link": ""}
        for n in range(41)
    ]
    for changes, field in (
        ({"title": ""}, "title"),
        ({"title": "T" * 81}, "title"),
        ({"title": "Line\nbreak"}, "title"),
        ({"year": "Y" * 21}, "year"),
        ({"tagline": "T" * 301}, "tagline"),
        ({"button_label": ""}, "button_label"),
        ({"button_label": "B" * 31}, "button_label"),
        ({"logo_asset_id": "not-a-uuid"}, "logo_asset_id"),
        ({"cards": too_many}, "cards"),
        ({"cards": [{**cards[0], "title": ""}]}, "cards.0.title"),
        ({"cards": [{**cards[0], "title": "C" * 121}]}, "cards.0.title"),
        ({"cards": [{**cards[0], "id": ""}]}, "cards.0.id"),
        ({"cards": [{**cards[0], "id": "x" * 41}]}, "cards.0.id"),
        ({"cards": [{**cards[0], "id": "has space"}]}, "cards.0.id"),
        ({"cards": [{**cards[0], "colour": "red"}]}, "cards.0.colour"),
        ({"subtitle": "extra"}, "subtitle"),
    ):
        resp = await admin.put("/opening", _body(editor, **changes))
        assert_error(resp, 422, "VALIDATION_FAILED")
        assert _fields(resp) == [field], (changes, resp.text)

    # The URLs the editor loaded may be sent back; they are ignored.
    echoed = _body(editor, logo_url="/media/elsewhere.webp", cards=editor["opening"]["cards"][:1])
    assert set(echoed["cards"][0]) == {"id", "title", "icon", "image_asset_id", "image_url", "link"}
    resp = await admin.put("/opening", echoed)
    assert resp.status_code == 200, resp.text
    assert resp.json()["opening"]["logo_url"] is None
    stored = await world.factory.fetchval(
        "SELECT opening FROM programmes WHERE id = :p", p=world.programme_id
    )
    assert "logo_url" not in stored and "image_url" not in stored["cards"][0]
    editor = resp.json()

    # The whole page is sent: a missing value is refused, not quietly blanked.
    body = _body(editor)
    del body["year"]
    assert _fields(await admin.put("/opening", body)) == ["year"]

    # Card ids are the editor's keys: two cards cannot share one.
    resp = await admin.put(
        "/opening", _body(editor, cards=[cards[0], cards[1], {**cards[2], "id": cards[0]["id"]}])
    )
    error = assert_error(resp, 422, "VALIDATION_FAILED")
    assert error["details"]["fields"][0]["field"] == "cards.2.id"

    # The refused saves changed nothing.
    assert (await _editor(admin))["settings_version"] == editor["settings_version"]


async def test_images_must_exist_and_be_uploaded_for_their_place(world: World) -> None:
    admin = await world.admin()
    logo = await _upload(admin, "programme_logo")
    picture = await _upload(admin, "event_logo")
    photo = await _upload(admin, "nominee_photo")
    editor = await _editor(admin)

    for changes, field, words in (
        ({"logo_asset_id": str(uuid.uuid4())}, "logo_asset_id", "not found"),
        ({"logo_asset_id": picture}, "logo_asset_id", "not uploaded as a logo"),
        (
            {"cards": [{**_body(editor)["cards"][0], "image_asset_id": logo}]},
            "cards.0.image_asset_id",
            "card 1",
        ),
        (
            {"cards": [*_body(editor)["cards"][:3], {**_body(editor)["cards"][3], "image_asset_id": photo}]},
            "cards.3.image_asset_id",
            "card 4 was not uploaded as a card picture",
        ),
        (
            {"cards": [{**_body(editor)["cards"][0], "image_asset_id": str(uuid.uuid4())}]},
            "cards.0.image_asset_id",
            "card 1 was not found",
        ),
    ):
        resp = await admin.put("/opening", _body(editor, **changes))
        error = assert_error(resp, 422, "VALIDATION_FAILED")
        assert error["details"]["fields"] == [{"field": field, "message": error["message"]}]
        assert words in error["message"], error["message"]

    body = _body(editor, logo_asset_id=logo)
    body["cards"][1]["image_asset_id"] = picture
    resp = await admin.put("/opening", body)
    assert resp.status_code == 200, resp.text
    page = resp.json()["opening"]
    assert page["logo_asset_id"] == logo and page["logo_url"].startswith("/media/")
    assert page["cards"][1]["image_asset_id"] == picture and page["cards"][1]["image_url"].startswith(
        "/media/"
    )
    assert page["cards"][0]["image_url"] is None

    public = await _public(world)
    assert public["logo_url"] == page["logo_url"]
    assert public["cards"][1]["image_url"] == page["cards"][1]["image_url"]
    # The public page carries URLs, never asset ids.
    assert logo not in json.dumps(public) and picture not in json.dumps(public)

    # Removing the logo goes back to the built-in mark.
    resp = await admin.put("/opening", _body(resp.json(), logo_asset_id=None))
    assert resp.status_code == 200, resp.text
    assert (await _public(world))["logo_url"] is None


async def test_a_stale_version_is_refused(world: World) -> None:
    first = await world.admin()
    second = await world.admin(fresh=True)
    loaded = await _editor(first)
    assert (await _editor(second))["settings_version"] == loaded["settings_version"]

    resp = await first.put("/opening", _body(loaded, button_label="PLAY NOW"))
    assert resp.status_code == 200, resp.text
    resp = await second.put("/opening", _body(loaded, button_label="GO"))
    assert_error(resp, 409, "STALE_SETTINGS")
    assert (await _public(world))["button_label"] == "PLAY NOW"
    resp = await second.delete(f"/opening?settings_version={loaded['settings_version']}")
    assert_error(resp, 409, "STALE_SETTINGS")

    # The page shares the programme's settings version with the programme settings form.
    current = await _editor(first)
    patched = await first.patch(
        "/programme", {"settings_version": current["settings_version"], "help_contact": "Desk B"}
    )
    assert patched.status_code == 200, patched.text
    assert_error(await first.put("/opening", _body(current)), 409, "STALE_SETTINGS")
    stale = await first.patch(
        "/programme", {"settings_version": current["settings_version"], "help_contact": "Desk C"}
    )
    assert_error(stale, 409, "STALE_SETTINGS")
    assert (await _editor(first))["settings_version"] == patched.json()["settings_version"]


async def test_reset_goes_back_to_the_built_in_page(world: World) -> None:
    await _name(world, "Dhanvarsha 2026", "Gold rain.")
    admin = await world.admin()
    editor = await _editor(admin)
    resp = await admin.put(
        "/opening", _body(editor, title="Something else", cards=_body(editor)["cards"][:2])
    )
    assert resp.status_code == 200, resp.text
    saved = resp.json()

    # The version comes in the body (or as a query parameter); without it nothing happens.
    missing = await admin.delete("/opening")
    assert_error(missing, 422, "VALIDATION_FAILED")
    assert _fields(missing) == ["settings_version"]
    resp = await admin.http.request(
        "DELETE",
        f"{ADMIN_API}/opening",
        json={"settings_version": saved["settings_version"]},
        headers={"X-CSRF-Token": admin.csrf},
    )
    assert resp.status_code == 200, resp.text
    reset = resp.json()
    assert reset["settings_version"] == saved["settings_version"] + 1
    assert reset["opening"] == reset["defaults"] and reset["opening"]["title"] == "Dhanvarsha"
    assert (
        await world.factory.fetchval("SELECT opening FROM programmes WHERE id = :p", p=world.programme_id)
        == {}
    )
    public = await _public(world)
    assert (public["title"], public["tagline"], len(public["cards"])) == ("Dhanvarsha", "Gold rain.", 13)

    resp = await admin.delete(f"/opening?settings_version={reset['settings_version']}")
    assert resp.status_code == 200, resp.text
    assert resp.json()["settings_version"] == reset["settings_version"] + 1
    actions = await world.factory.fetch(
        "SELECT action FROM audit_log WHERE programme_id = :p AND action LIKE 'opening.%' ORDER BY id",
        p=world.programme_id,
    )
    assert [a.action for a in actions] == ["opening.updated", "opening.reset", "opening.reset"]


async def test_editor_needs_an_organiser_and_the_public_page_does_not(world: World) -> None:
    anonymous = world.http()
    assert_error(await anonymous.get(f"{ADMIN_API}/opening"), 401, "UNAUTHENTICATED")
    assert_error(await anonymous.put(f"{ADMIN_API}/opening", json={}), 401, "UNAUTHENTICATED")
    assert_error(await anonymous.delete(f"{ADMIN_API}/opening"), 401, "UNAUTHENTICATED")
    assert (await anonymous.get(f"{API}/public/opening")).status_code == 200

    admin = await world.admin()
    editor = await _editor(admin)
    resp = await admin.http.put(f"{ADMIN_API}/opening", json=_body(editor), headers={"X-CSRF-Token": "wrong"})
    assert_error(resp, 403, "CSRF_FAILED")
    resp = await admin.http.delete(
        f"{ADMIN_API}/opening?settings_version={editor['settings_version']}",
        headers={"X-CSRF-Token": "wrong"},
    )
    assert_error(resp, 403, "CSRF_FAILED")


async def test_unexpected_stored_values_fall_back_to_the_built_in_page(world: World) -> None:
    """The API never writes these, but a hand-edited row must not break the projector."""
    await _name(world, "Dhanvarsha 2026")
    stored = {
        "title": 42,
        "tagline": "Hand edited",
        "logo_asset_id": "not-a-uuid",
        "cards": [
            {"id": "a", "title": "Known", "icon": "trophy", "link": "/vote"},
            {"id": "b", "title": "Retired icon", "icon": "no-such-icon", "link": "javascript:alert(1)"},
            {"id": "", "title": "No id"},
            "junk",
        ],
    }
    await world.factory.execute(
        "UPDATE programmes SET opening = CAST(:o AS jsonb) WHERE id = :p",
        o=json.dumps(stored),
        p=world.programme_id,
    )
    public = await _public(world)
    assert (public["title"], public["year"], public["tagline"], public["button_label"]) == (
        "Dhanvarsha",
        "2026",
        "Hand edited",
        "PLAY GAME",
    )
    assert public["logo_url"] is None
    assert public["cards"] == [
        {"id": "a", "title": "Known", "icon": "trophy", "image_url": None, "link": "/vote"},
        {"id": "b", "title": "Retired icon", "icon": "star", "image_url": None, "link": ""},
    ]
    # The editor loads the same page, so saving it repairs the row.
    admin = await world.admin()
    resp = await admin.put("/opening", _body(await _editor(admin)))
    assert resp.status_code == 200, resp.text
    row = await world.factory.fetchval("SELECT opening FROM programmes WHERE id = :p", p=world.programme_id)
    assert row["title"] == "Dhanvarsha" and [c["id"] for c in row["cards"]] == ["a", "b"]
