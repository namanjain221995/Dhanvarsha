"""Team ballots list each team's members for voters; the vote is still for the whole team."""

from __future__ import annotations

import uuid
from typing import Any

from helpers import World, assert_error


def _nominee(payload: dict[str, Any], nominee_id: str) -> dict[str, Any]:
    for ballot in payload["ballots"]:
        for nominee in ballot["nominees"]:
            if nominee["id"] == nominee_id:
                return nominee  # type: ignore[no-any-return]
    raise AssertionError(f"nominee {nominee_id} not in payload")


async def test_team_members_are_cleaned_saved_and_shown_to_voters(world: World) -> None:
    admin = await world.admin()
    event = await world.event(nominees=1)
    resp = await admin.post(
        f"/ballots/{event.ballot.id}/nominees",
        {"name": "Lantern Lab", "members": ["  Asha   Rao ", "Ravi Kumar", "asha rao", ""]},
    )
    assert resp.status_code == 201, resp.text
    nominee_id = resp.json()["id"]
    members = ["Asha Rao", "Ravi Kumar"]
    assert (
        _nominee((await admin.get(f"/events/{event.id}")).json()["event"], nominee_id)["members"] == members
    )
    assert _nominee((await admin.get(f"/events/{event.id}/preview")).json(), nominee_id)["members"] == members

    assert (await admin.open_event(event.id)).status_code == 200
    person = await world.participant()
    shown = await person.event(event.id)
    assert shown.status_code == 200, shown.text
    assert _nominee(shown.json(), nominee_id)["members"] == members
    other = _nominee(shown.json(), str(event.ballot.nominee_ids[0]))
    assert other["members"] == []

    # The roster is presentation only: it can be corrected after voting opens, and the
    # correction is audited. The vote goes to the team, not to a member.
    resp = await admin.patch(f"/nominees/{nominee_id}", {"members": [*members, "Meera Pillai"]})
    assert resp.status_code == 200, resp.text
    assert _nominee((await person.event(event.id)).json(), nominee_id)["members"] == [
        *members,
        "Meera Pillai",
    ]
    audit = await world.factory.fetchrow(
        "SELECT action, details FROM audit_log WHERE target_id = :n ORDER BY id DESC LIMIT 1", n=nominee_id
    )
    assert audit.action == "nominee.corrected"
    assert audit.details["members"] == {"from": members, "to": [*members, "Meera Pillai"]}

    vote = await person.vote(event.ballot.id, uuid.UUID(nominee_id))
    assert vote.status_code == 201, vote.text
    assert vote.json()["receipt"]["nominee_name"] == "Lantern Lab"


async def test_team_member_names_are_validated(world: World) -> None:
    admin = await world.admin()
    event = await world.event(nominees=1)
    path = f"/ballots/{event.ballot.id}/nominees"
    for members in (
        [f"Person {i}" for i in range(31)],
        ["Asha\nRao"],
        ["x" * 121],
        "Asha Rao, Ravi Kumar",
    ):
        assert_error(await admin.post(path, {"name": "Team", "members": members}), 422, "VALIDATION_FAILED")
    nominee = str(event.ballot.nominee_ids[0])
    assert_error(
        await admin.patch(f"/nominees/{nominee}", {"members": ["tab\there"]}), 422, "VALIDATION_FAILED"
    )
    rows = await world.factory.fetch("SELECT members FROM nominees WHERE ballot_id = :b", b=event.ballot.id)
    assert [r.members for r in rows] == [[]]
