"""Regressions for issues found in the independent security review."""

from __future__ import annotations

import io
import uuid
from typing import Any

import pytest
from PIL import Image

from helpers import API, World, assert_error
from stagevote.config import get_settings
from stagevote.errors import AppError
from stagevote.live.hub import Connection, LiveHub
from stagevote.security import new_token
from stagevote.services.assets import process_image


async def failures(world: World) -> int:
    return int(
        await world.factory.fetchval(
            "SELECT coalesce(sum(count), 0) FROM rate_limit_buckets WHERE key = :k",
            k=f"login_fail:ip:{world.ip}",
        )
    )


async def test_unknown_links_are_limited_per_address_but_refusals_elsewhere_are_not(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A 256-bit link cannot be guessed; the per-address limit only stops noise. The
    owner's link opened on a second phone is refused every time but never counted, so
    one shared link cannot pause sign-in for a whole venue behind one address."""
    monkeypatch.setattr(get_settings(), "login_failures_per_ip", 5)
    owner = await world.participant()
    elsewhere = world.browser(owner.info)
    for _ in range(8):
        assert_error(await elsewhere.enter(), 403, "LINK_IN_USE")
        assert (await elsewhere.check()).json() == {"state": "in_use"}
    assert await failures(world) == 0

    guesser = world.http()
    for i in range(5):
        if i % 2:
            resp = await guesser.post(f"{API}/auth/link/check", json={"token": new_token()})
            assert resp.json() == {"state": "invalid"}
        else:
            assert_error(
                await guesser.post(f"{API}/auth/link", json={"token": new_token()}), 401, "LINK_INVALID"
            )
    assert await failures(world) == 5
    for path in ("/auth/link", "/auth/link/check"):
        error = assert_error(
            await guesser.post(f"{API}{path}", json={"token": new_token()}), 429, "RATE_LIMITED"
        )
        assert error["details"]["retry_after_seconds"] > 0
    # The pause covers the whole address, a real link included, until the window ends.
    assert_error(await world.browser(owner.info).enter(), 429, "RATE_LIMITED")


async def test_frozen_nominee_cannot_be_renamed_into_a_sibling(world: World) -> None:
    admin = await world.admin()
    event = await world.live_event(nominees=2)  # "Act 1.1" and "Act 1.2", frozen when opened
    first, second = event.ballot.nominee_ids
    resp = await admin.patch(f"/nominees/{first}", {"name": "Act 1.2", "reason": "swap"})
    assert_error(resp, 409, "NOMINEE_FROZEN")
    resp = await admin.patch(f"/nominees/{first}", {"name": "Act 1.1.", "reason": "typo"})
    assert resp.status_code == 200, resp.text
    names = {
        r.id: r.name
        for r in await world.factory.fetch(
            "SELECT id, name FROM nominees WHERE id = ANY(:ids)", ids=[first, second]
        )
    }
    assert names == {first: "Act 1.1.", second: "Act 1.2"}


def _conn(principal: uuid.UUID, session: uuid.UUID) -> Connection:
    return Connection(
        ws=None, role="participant", programme_id=None, session_id=session, principal_id=principal
    )  # type: ignore[arg-type]


async def test_revocation_closes_only_the_named_sessions() -> None:
    hub = LiveHub("postgresql+asyncpg://unused@localhost/unused")
    person, old_session, new_session = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    old, new = _conn(person, old_session), _conn(person, new_session)
    hub.register(old)
    hub.register(new)
    event: dict[str, Any] = {
        "id": 7,
        "p": None,
        "t": "session",
        "d": {"pid": str(person), "sids": [str(old_session)]},
    }
    hub.dispatch(event)
    assert old.close_code == 4401
    assert new.close_code is None


@pytest.mark.filterwarnings("ignore::PIL.Image.DecompressionBombWarning")
def test_images_over_40_megapixels_are_rejected_before_decoding() -> None:
    buffer = io.BytesIO()
    Image.new("L", (8000, 6000)).save(buffer, format="PNG")  # 48 MP, compresses to a small file
    with pytest.raises(AppError) as caught:
        process_image(buffer.getvalue(), "event_logo")
    assert caught.value.code == "IMAGE_TOO_LARGE"
    # A picture for the big screen may be larger; it is stored at most 3840 pixels wide.
    _, width, height = process_image(buffer.getvalue(), "opening_media")
    assert (width, height) == (3840, 2880)
