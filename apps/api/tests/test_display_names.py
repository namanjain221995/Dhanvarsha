"""Voters invited without a name are greeted by the name in their email address."""

from __future__ import annotations

from helpers import World, drain
from stagevote.mailer.providers import RecordingProvider
from stagevote.services.participants import name_from_email
from stagevote.worker.runner import Worker


def test_the_name_comes_from_the_part_before_the_at_sign() -> None:
    assert name_from_email("naman.jain@techsarasolutions.com") == "Naman Jain"
    assert name_from_email("SOHAM.PIPROTAR@example.com") == "Soham Piprotar"
    assert name_from_email("namanjain34710@gmail.com") == "Namanjain"
    assert name_from_email("a_b-c+tag@example.com") == "A B C"
    assert name_from_email("1234@example.com") == ""


async def test_the_voter_page_uses_the_address_when_no_name_was_given(world: World) -> None:
    info = await world.factory.create_participant(
        world.programme_id, email="asha.rao@example.com", display_name=""
    )
    voter = world.browser(info)
    assert (await voter.enter()).status_code == 200
    snapshot = (await voter.snapshot()).json()
    assert snapshot["participant"]["display_name"] == "Asha Rao"


async def test_a_name_given_by_the_organisers_is_kept(world: World) -> None:
    voter = await world.participant()
    assert (await voter.snapshot()).json()["participant"]["display_name"] == "Test Participant"


async def test_the_invitation_greets_by_the_name_in_the_address(
    world: World, worker: Worker, provider: RecordingProvider
) -> None:
    info = await world.factory.create_participant(
        world.programme_id, activated=False, email="ravi.kumar@example.com", display_name=""
    )
    resp = await (await world.admin()).post(f"/participants/{info.id}/invite")
    assert resp.status_code == 200, resp.text
    await worker.email_tick()
    await drain(worker)
    [message] = [m for m in provider.sent if m.to == "ravi.kumar@example.com"]
    assert "Hi Ravi Kumar," in message.text
