"""Regressions for issues found in the independent security review."""

from __future__ import annotations

import asyncio
import io
import uuid
from typing import Any

import pytest
from PIL import Image

from helpers import API, PARTICIPANT_PASSWORD, World, assert_error
from stagevote.errors import AppError
from stagevote.live.hub import Connection, LiveHub
from stagevote.services.assets import _process


async def test_parallel_guesses_cannot_exceed_the_per_identifier_limit(world: World) -> None:
    """The attempt is counted before the password check, so a burst cannot slip past."""
    person = await world.participant(login=False)
    clients = [world.http() for _ in range(20)]

    async def guess(i: int) -> int:
        resp = await clients[i].post(
            f"{API}/auth/login", json={"user_id": person.info.email, "password": f"wrong-{i}"}
        )
        return resp.status_code

    statuses = await asyncio.gather(*(guess(i) for i in range(20)))
    assert statuses.count(401) <= 8  # LOGIN_FAILURES_PER_ACCOUNT
    assert statuses.count(429) >= 12
    # Even the right password is refused while the identifier is locked.
    assert_error(
        await world.browser(person.info).login(PARTICIPANT_PASSWORD, user_id=person.info.email),
        429,
        "RATE_LIMITED",
    )


async def test_lockout_does_not_reveal_whether_an_account_exists(world: World) -> None:
    person = await world.participant(login=False)
    unknown = f"nobody-{uuid.uuid4().hex[:8]}@example.com"
    http = world.http()

    async def lock(identifier: str) -> list[int]:
        return [
            (await http.post(f"{API}/auth/login", json={"user_id": identifier, "password": "x"})).status_code
            for _ in range(10)
        ]

    assert await lock(person.info.email) == await lock(unknown)


async def test_successful_login_clears_the_identifier_counter(world: World) -> None:
    person = await world.participant(login=False)
    http = world.http()
    for _ in range(5):
        resp = await http.post(f"{API}/auth/login", json={"user_id": person.info.email, "password": "nope"})
        assert resp.status_code == 401
    browser = world.browser(person.info)
    assert (await browser.login(user_id=person.info.email)).status_code == 200
    assert (await browser.logout()).status_code == 200
    for _ in range(7):
        resp = await http.post(f"{API}/auth/login", json={"user_id": person.info.email, "password": "nope"})
        assert resp.status_code == 401  # the earlier failures no longer count


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
        _process(buffer.getvalue(), "event_logo")
    assert caught.value.code == "IMAGE_TOO_LARGE"
