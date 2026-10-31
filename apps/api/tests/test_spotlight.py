"""The spotlight screen: the organiser's people, the Show / Send back / Hide all buttons, and
the public view the big screen polls.

The public view needs no sign-in and names only people already revealed: a face-down card is
an id and nothing else. The spotlight has its own version and its own column, so nothing here
touches the programme settings, the opening page, events or votes.
"""

from __future__ import annotations

import io
import json
import uuid
from typing import Any

from PIL import Image

from helpers import ADMIN_API, API, AdminClient, World, assert_error

MP4 = b"\x00\x00\x00\x18ftypmp42" + b"\x00\x00\x00\x00mp42isom" + bytes(range(256)) * 40


def _png(width: int = 90, height: int = 120) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (width, height), (212, 175, 55)).save(buf, format="PNG")
    return buf.getvalue()


async def _public(world: World) -> dict[str, Any]:
    resp = await world.http(origin=None).get(f"{API}/public/spotlight")
    assert resp.status_code == 200, resp.text
    assert resp.headers["cache-control"].startswith("no-store")
    body: dict[str, Any] = resp.json()
    return body


async def _editor(admin: AdminClient) -> dict[str, Any]:
    resp = await admin.get("/spotlight")
    assert resp.status_code == 200, resp.text
    body: dict[str, Any] = resp.json()["spotlight"]
    return body


async def _picture(admin: AdminClient, name: str = "photo.png") -> dict[str, Any]:
    resp = await admin.upload_media(_png(), name, "image/png")
    assert resp.status_code == 201, resp.text
    body: dict[str, Any] = resp.json()
    return body


def _body(editor: dict[str, Any], **changes: Any) -> dict[str, Any]:
    """A save request made from what the editor loaded, as the console sends it."""
    body: dict[str, Any] = {
        "version": editor["version"],
        "title": editor["title"],
        "subtitle": editor["subtitle"],
        "hold_seconds": editor["hold_seconds"],
        "entries": [{k: e[k] for k in ("id", "name", "detail", "image_asset_id")} for e in editor["entries"]],
    }
    body.update(changes)
    return body


async def _save_people(
    admin: AdminClient, *people: tuple[str, str, str | None], **changes: Any
) -> dict[str, Any]:
    editor = await _editor(admin)
    entries = [
        {"id": f"p{index}", "name": name, "detail": detail, "image_asset_id": image}
        for index, (name, detail, image) in enumerate(people, start=1)
    ]
    resp = await admin.put("/spotlight", _body(editor, entries=entries, **changes))
    assert resp.status_code == 200, resp.text
    body: dict[str, Any] = resp.json()["spotlight"]
    return body


async def _settings_and_opening(world: World) -> tuple[Any, ...]:
    row = await world.factory.fetchrow(
        "SELECT settings_version, opening::text, state_version, live_version FROM programmes WHERE id = :p",
        p=world.programme_id,
    )
    return tuple(row)


async def test_an_empty_spotlight_has_the_built_in_title(world: World) -> None:
    public = await _public(world)
    assert public == {
        "title": "Spotlight",
        "subtitle": "",
        "logo_url": None,
        "hold_seconds": 10,
        "epoch": 0,
        "seq": 0,
        "shown": None,
        "cards": [],
    }
    admin = await world.admin()
    assert await _editor(admin) == {
        "version": 0,
        "title": "Spotlight",
        "subtitle": "",
        "hold_seconds": 10,
        "entries": [],
        "revealed": [],
        "seq": 0,
        "shown": None,
        "epoch": 0,
    }


async def test_people_are_saved_in_order_and_face_down_cards_give_nothing_away(world: World) -> None:
    admin = await world.admin()
    before = await _settings_and_opening(world)
    asha = await _picture(admin, "Asha portrait.png")
    ravi = await _picture(admin, "Ravi portrait.png")
    saved = await _save_people(
        admin,
        ("Asha Mehta", "Recruiter of the Year", asha["id"]),
        ("Ravi Shah", "", ravi["id"]),
        ("Neel Patel", "Rising Star", None),
        title="Professional Honours",
        subtitle="Part One",
        hold_seconds=8,
    )
    assert saved["version"] == 1
    assert (saved["title"], saved["subtitle"], saved["hold_seconds"]) == (
        "Professional Honours",
        "Part One",
        8,
    )
    assert [e["name"] for e in saved["entries"]] == ["Asha Mehta", "Ravi Shah", "Neel Patel"]
    assert saved["entries"][0]["image"] == {
        "url": asha["url"],
        "name": "Asha portrait.png",
        "byte_size": asha["byte_size"],
        "width": 90,
        "height": 120,
    }
    assert saved["entries"][2]["image"] is None
    assert saved == await _editor(admin)

    public = await _public(world)
    assert (public["title"], public["subtitle"], public["hold_seconds"]) == (
        "Professional Honours",
        "Part One",
        8,
    )
    assert public["cards"] == [
        {"id": "p1", "revealed": False},
        {"id": "p2", "revealed": False},
        {"id": "p3", "revealed": False},
    ]
    text = json.dumps(public)
    for secret in ("Asha", "Ravi", "Neel", "Recruiter", "Rising", asha["url"], asha["id"]):
        assert secret not in text

    # Its own column and version: the programme settings, the opening page and the live state
    # are exactly as they were.
    assert await _settings_and_opening(world) == before


async def test_show_reveals_one_person_on_every_screen(world: World) -> None:
    admin = await world.admin()
    asha = await _picture(admin)
    await _save_people(admin, ("Asha Mehta", "Recruiter of the Year", asha["id"]), ("Ravi Shah", "", None))
    before = await _settings_and_opening(world)

    resp = await admin.post("/spotlight/show", {"entry_id": "p1"})
    assert resp.status_code == 200, resp.text
    editor = resp.json()["spotlight"]
    assert (editor["revealed"], editor["seq"]) == (["p1"], 1)
    assert editor["shown"] == {"id": "p1", "seq": 1, "back": False, "first": True}

    public = await _public(world)
    assert public["seq"] == 1 and public["shown"] == {"id": "p1", "seq": 1, "back": False, "first": True}
    assert public["cards"] == [
        {
            "id": "p1",
            "revealed": True,
            "name": "Asha Mehta",
            "detail": "Recruiter of the Year",
            "image_url": asha["url"],
        },
        {"id": "p2", "revealed": False},
    ]
    assert "Ravi" not in json.dumps(public)

    # Showing the same person again plays it again (a new seq) but it is no longer a first reveal.
    resp = await admin.post("/spotlight/show", {"entry_id": "p1"})
    assert resp.json()["spotlight"]["shown"] == {"id": "p1", "seq": 2, "back": False, "first": False}
    assert resp.json()["spotlight"]["revealed"] == ["p1"]

    # The next person: revealed in order, without a picture too.
    resp = await admin.post("/spotlight/show", {"entry_id": "p2"})
    assert resp.json()["spotlight"]["revealed"] == ["p1", "p2"]
    public = await _public(world)
    assert public["cards"][1] == {
        "id": "p2",
        "revealed": True,
        "name": "Ravi Shah",
        "detail": "",
        "image_url": None,
    }
    assert public["shown"] == {"id": "p2", "seq": 3, "back": False, "first": True}

    # None of this touches the programme settings, the opening page or the live state.
    assert await _settings_and_opening(world) == before
    actions = [
        r.action
        for r in await world.factory.fetch(
            "SELECT action FROM audit_log WHERE programme_id = :p AND action LIKE 'spotlight.%' ORDER BY id",
            p=world.programme_id,
        )
    ]
    assert actions == ["spotlight.updated", "spotlight.shown", "spotlight.shown", "spotlight.shown"]


async def test_send_back_and_hide_all(world: World) -> None:
    admin = await world.admin()
    await _save_people(admin, ("Asha Mehta", "", None), ("Ravi Shah", "", None))
    # Nothing in the middle yet: sending back changes nothing.
    resp = await admin.post("/spotlight/back")
    assert resp.status_code == 200 and resp.json()["spotlight"]["shown"] is None

    await admin.post("/spotlight/show", {"entry_id": "p2"})
    resp = await admin.post("/spotlight/back")
    assert resp.json()["spotlight"]["shown"] == {"id": "p2", "seq": 1, "back": True, "first": True}
    # Twice is the same as once.
    resp = await admin.post("/spotlight/back")
    assert resp.json()["spotlight"]["shown"] == {"id": "p2", "seq": 1, "back": True, "first": True}
    assert (await _public(world))["cards"][1]["revealed"] is True

    resp = await admin.post("/spotlight/hide-all")
    assert resp.status_code == 200, resp.text
    editor = resp.json()["spotlight"]
    assert (editor["revealed"], editor["shown"], editor["epoch"], editor["seq"]) == ([], None, 1, 1)
    assert [e["name"] for e in editor["entries"]] == ["Asha Mehta", "Ravi Shah"]
    public = await _public(world)
    assert public["epoch"] == 1 and public["shown"] is None
    assert all(card == {"id": card["id"], "revealed": False} for card in public["cards"])

    # After hiding, a person's next Show is a first reveal again.
    resp = await admin.post("/spotlight/show", {"entry_id": "p2"})
    assert resp.json()["spotlight"]["shown"] == {"id": "p2", "seq": 2, "back": False, "first": True}


async def test_only_saved_people_can_be_shown(world: World) -> None:
    admin = await world.admin()
    await _save_people(admin, ("Asha Mehta", "", None))
    error = assert_error(await admin.post("/spotlight/show", {"entry_id": "p9"}), 404, "NOT_FOUND")
    assert "Save first" in error["message"]
    assert_error(await admin.post("/spotlight/show", {"entry_id": "has space"}), 422, "VALIDATION_FAILED")
    assert (await _editor(admin))["seq"] == 0


async def test_saving_keeps_the_live_state_of_the_people_still_there(world: World) -> None:
    admin = await world.admin()
    await _save_people(admin, ("Asha Mehta", "", None), ("Ravi Shah", "", None), ("Neel Patel", "", None))
    await admin.post("/spotlight/show", {"entry_id": "p1"})
    await admin.post("/spotlight/show", {"entry_id": "p3"})
    editor = await _editor(admin)

    # Rename Asha, remove Neel (who is in the middle now), keep Ravi.
    entries = [
        {"id": "p1", "name": "Asha M. Mehta", "detail": "", "image_asset_id": None},
        {"id": "p2", "name": "Ravi Shah", "detail": "", "image_asset_id": None},
    ]
    resp = await admin.put("/spotlight", _body(editor, entries=entries))
    assert resp.status_code == 200, resp.text
    saved = resp.json()["spotlight"]
    assert (saved["version"], saved["revealed"], saved["shown"], saved["seq"]) == (2, ["p1"], None, 2)
    public = await _public(world)
    assert public["cards"] == [
        {"id": "p1", "revealed": True, "name": "Asha M. Mehta", "detail": "", "image_url": None},
        {"id": "p2", "revealed": False},
    ]


async def test_a_stale_version_is_refused(world: World) -> None:
    admin = await world.admin()
    editor = await _editor(admin)
    await _save_people(admin, ("Asha Mehta", "", None))
    error = assert_error(await admin.put("/spotlight", _body(editor, title="Late")), 409, "STALE_SPOTLIGHT")
    assert "Reload" in error["message"]
    # The live buttons need no version: an organiser's unsaved edits stay saveable.
    fresh = await _editor(admin)
    await admin.post("/spotlight/show", {"entry_id": "p1"})
    await admin.post("/spotlight/hide-all")
    resp = await admin.put("/spotlight", _body(fresh, title="Honours"))
    assert resp.status_code == 200, resp.text


async def test_fields_are_checked(world: World) -> None:
    admin = await world.admin()
    editor = await _editor(admin)
    person = {"id": "p1", "name": "Asha", "detail": "", "image_asset_id": None}
    for changes, field in (
        ({"title": ""}, "title"),
        ({"title": "x" * 81}, "title"),
        ({"subtitle": "a\nb"}, "subtitle"),
        ({"hold_seconds": -1}, "hold_seconds"),
        ({"hold_seconds": 601}, "hold_seconds"),
        ({"entries": [{**person, "name": ""}]}, "entries.0.name"),
        ({"entries": [{**person, "name": "x" * 81}]}, "entries.0.name"),
        ({"entries": [{**person, "detail": "x" * 121}]}, "entries.0.detail"),
        ({"entries": [{**person, "id": "bad id"}]}, "entries.0.id"),
        ({"entries": [person] * 61}, "entries"),
    ):
        error = assert_error(
            await admin.put("/spotlight", _body(editor, **changes)), 422, "VALIDATION_FAILED"
        )
        assert [f["field"] for f in error["details"]["fields"]] == [field], (changes, error)
    error = assert_error(
        await admin.put("/spotlight", _body(editor, entries=[person, {**person, "name": "Twin"}])),
        422,
        "VALIDATION_FAILED",
    )
    assert error["details"]["fields"] == [{"field": "entries.1.id", "message": error["message"]}]
    assert (await _editor(admin))["version"] == 0


async def test_pictures_must_be_uploaded_images(world: World) -> None:
    admin = await world.admin()
    video = await admin.upload_media(MP4, "clip.mp4", "video/mp4")
    assert video.status_code == 201, video.text
    buf = io.BytesIO()
    Image.new("RGB", (64, 64)).save(buf, format="PNG")
    logo = await admin.http.post(
        f"{ADMIN_API}/assets",
        data={"kind": "event_logo"},
        files={"file": ("logo.png", buf.getvalue(), "image/png")},
        headers={"X-CSRF-Token": admin.csrf},
    )
    assert logo.status_code == 201, logo.text
    editor = await _editor(admin)
    for image, words in (
        (video.json()["id"], "is not a picture"),
        (logo.json()["id"], "is not a picture"),
        (str(uuid.uuid4()), "was not found"),
    ):
        body = _body(editor, entries=[{"id": "p1", "name": "Asha", "detail": "", "image_asset_id": image}])
        error = assert_error(await admin.put("/spotlight", body), 422, "VALIDATION_FAILED")
        assert error["details"]["fields"] == [
            {"field": "entries.0.image_asset_id", "message": error["message"]}
        ]
        assert words in error["message"], error["message"]


async def test_the_editor_needs_an_organiser_and_the_screen_does_not(world: World) -> None:
    anonymous = world.http()
    for method, path in (
        ("GET", "/spotlight"),
        ("PUT", "/spotlight"),
        ("POST", "/spotlight/show"),
        ("POST", "/spotlight/back"),
        ("POST", "/spotlight/hide-all"),
    ):
        resp = await anonymous.request(method, f"{ADMIN_API}{path}", json={})
        assert resp.status_code == 401, (method, path, resp.text)
    admin = await world.admin()
    await _save_people(admin, ("Asha", "", None))
    # Without the CSRF token an organiser's browser cannot be made to press Show.
    resp = await admin.http.post(f"{ADMIN_API}/spotlight/show", json={"entry_id": "p1"})
    assert resp.status_code == 403, resp.text
    assert (await _public(world))["seq"] == 0


async def test_hand_edited_values_fall_back_to_defaults(world: World) -> None:
    await world.factory.execute(
        "UPDATE programmes SET spotlight = CAST(:s AS jsonb) WHERE id = :p",
        s=json.dumps(
            {
                "version": "two",
                "title": 7,
                "hold_seconds": True,
                "entries": [
                    {"id": "ok", "name": "Asha", "image_asset_id": "not-a-uuid"},
                    {"id": "ok", "name": "Duplicate"},
                    {"id": "no name"},
                    "junk",
                ],
                "revealed": ["ok", "ok", "gone", 5],
                "shown": {"entry_id": "gone", "seq": 3},
                "seq": -4,
                "epoch": 2,
            }
        ),
        p=world.programme_id,
    )
    public = await _public(world)
    assert public == {
        "title": "Spotlight",
        "subtitle": "",
        "logo_url": None,
        "hold_seconds": 10,
        "epoch": 2,
        "seq": 0,
        "shown": None,
        "cards": [{"id": "ok", "revealed": True, "name": "Asha", "detail": "", "image_url": None}],
    }
    admin = await world.admin()
    editor = await _editor(admin)
    assert editor["version"] == 0
    resp = await admin.put("/spotlight", _body(editor))
    assert resp.status_code == 200, resp.text
