"""`stagevote load-polls`, organiser notes and the results the audience polls rely on.

The Dhanvarsha audience polls (stagevote/data/dhanvarsha_audience_polls.json) become one
round each, in file order, with one ballot whose title is the question and whose nominees
are the options. Organiser notes are in admin payloads only. Results carry the ballot's
description (P04: the audience favourite is not the official winner) and give 0 percent
when nobody voted.
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any

import pytest

from helpers import AdminClient, World, assert_error, dumps
from stagevote import cli
from stagevote.polls import DEFAULT_FILE, KEPT

POLLS: list[dict[str, Any]] = json.loads(DEFAULT_FILE.read_text(encoding="utf-8"))["polls"]
NAMES = [p["name"] for p in POLLS]
WIDTH = len(KEPT)


def line(outcome: str, name: str, note: str = "") -> str:
    return f"  {outcome:<{WIDTH}}  {name}{note}"


async def _events(admin: AdminClient) -> list[dict[str, Any]]:
    resp = await admin.get("/events")
    assert resp.status_code == 200, resp.text
    events: list[dict[str, Any]] = resp.json()["events"]
    return events


async def _state(world: World) -> tuple[Any, ...]:
    row = await world.factory.fetchrow(
        "SELECT state_version, live_version, "
        "(SELECT count(*) FROM audit_log WHERE programme_id = :p) AS audits, "
        "(SELECT count(*) FROM events WHERE programme_id = :p) AS events, "
        "(SELECT count(*) FROM ballots WHERE programme_id = :p) AS ballots, "
        "(SELECT max(version) FROM events WHERE programme_id = :p) AS versions "
        "FROM programmes WHERE id = :p",
        p=world.programme_id,
    )
    return tuple(row)


def _as_file(poll: dict[str, Any]) -> dict[str, Any]:
    """An admin event payload in the polls file's shape."""
    [ballot] = poll["ballots"]
    return {
        "name": poll["name"],
        "organiser_note": poll["organiser_note"],
        "ballot": {
            "kind": ballot["kind"],
            "title": ballot["title"],
            "description": ballot["description"],
            "options": [
                {"name": n["name"], "members": n["members"]} if n["members"] else {"name": n["name"]}
                for n in ballot["nominees"]
            ],
        },
    }


def _expected(poll: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in poll.items() if k != "id"}


async def test_polls_load_in_file_order_ahead_of_other_rounds_and_reload_changes_nothing(
    world: World, capsys: pytest.CaptureFixture[str]
) -> None:
    welcome = await world.event(name="Welcome")
    raffle = await world.event(name="Raffle")
    admin = await world.admin()
    capsys.readouterr()

    printed = await cli.load_polls()
    assert capsys.readouterr().out.splitlines() == printed
    assert printed[0].startswith("Loading 16 polls from ") and str(DEFAULT_FILE) in printed[0]
    assert printed[1:17] == [line("created", name) for name in NAMES]
    assert printed[17] == "Done: 16 created, 0 updated, 0 unchanged, 0 kept, 0 archived."

    events = await _events(admin)
    assert len(events) == 18
    assert [e["round"] for e in events] == list(range(1, 19))
    assert [_as_file(e) for e in events[:16]] == [_expected(p) for p in POLLS]
    # The other rounds follow the polls, in their own order.
    assert [(e["id"], e["name"]) for e in events[16:]] == [
        (str(welcome.id), "Welcome"),
        (str(raffle.id), "Raffle"),
    ]
    by_name = {e["name"]: e for e in events}
    assert {e["status"] for e in events[:16]} == {"upcoming"}
    assert {(e["description"], e["rules"]) for e in events[:16]} == {("", "")}

    # The brief's wording, checked here too: P01-P14 then the break polls B01 and B02. Round
    # names carry no id: voters see "TechSara Placement League", not "P04 — …".
    assert [p["id"] for p in POLLS] == [f"P{i:02d}" for i in range(1, 15)] + ["B01", "B02"]
    assert not [n for n in NAMES if " — " in n or n[:3] in {p["id"] for p in POLLS}]
    kinds = [by_name[n]["ballots"][0]["kind"] for n in NAMES]
    assert kinds == [
        "mcq", "mcq", "mcq", "team", "individual", "individual", "mcq", "individual",
        "mcq", "individual", "individual", "team", "mcq", "mcq", "mcq", "mcq",
    ]  # fmt: skip
    first = by_name["Before the event"]
    assert (
        first["organiser_note"]
        == "When to show: Before the opening video.\nSection: Main event poll sequence"
    )
    [question] = first["ballots"]
    assert question["title"] == "Dhanvarsha is about to begin! How are you feeling?"
    assert [n["name"] for n in question["nominees"]] == [
        "Super excited!",
        "Curious about the surprises",
        "Ready to cheer for my team",
        "Still settling in",
    ]
    [pairs] = by_name["TechSara Placement League"]["ballots"]
    assert pairs["description"] == "Audience Favourite. The official winner is decided by the highest bid."
    assert [(n["name"], n["members"]) for n in pairs["nominees"]] == [
        ("Jaimin & Sakshi", []),
        ("Jahnvi & Riya", []),
        ("Lokesh & Jackson", []),
        ("Rutu & Avinash", []),
        ("Shubham & Yash", []),
    ]
    [ideas] = by_name["TechSara Shark Tank"]["ballots"]
    assert (ideas["kind"], ideas["title"]) == (
        "team",
        "Which team presented the best idea for TechSara's growth?",
    )
    assert [(n["name"], n["members"]) for n in ideas["nominees"]] == [
        ("Swayam AI", ["Naman", "Heet"]),
        ("Algo Lens", ["Suyash", "Maithilya"]),
        ("Project YOG", ["Srishti", "Ishita G.", "Nikita"]),
    ]
    assert "During the break" in by_name["Break question 1"]["organiser_note"]

    # Live control offers P01 next, with its note; participants see 18 rounds and no note.
    live = (await admin.get("/live")).json()
    assert (live["next_event"]["name"], live["next_event"]["organiser_note"]) == (
        first["name"],
        first["organiser_note"],
    )
    assert [e["organiser_note"] for e in live["events"][:16]] == [p["organiser_note"] for p in POLLS]
    voter = await world.participant()
    snapshot = await voter.refresh()
    assert snapshot["event_count"] == 18
    assert "organiser_note" not in dumps(snapshot) and "When to show" not in dumps(snapshot)

    audits = await world.factory.fetch(
        "SELECT action, count(*) AS n FROM audit_log WHERE programme_id = :p "
        "AND details->>'via' = 'load-polls' GROUP BY action ORDER BY action",
        p=world.programme_id,
    )
    assert [(a.action, a.n) for a in audits] == [("event.created", 16), ("event.reordered", 1)]
    assert (
        await world.factory.fetchval(
            "SELECT details->>'created' FROM audit_log WHERE programme_id = :p AND action = 'polls.loaded'",
            p=world.programme_id,
        )
        == "16"
    )

    # Loading the same file again changes nothing at all.
    before = await _state(world)
    again = await cli.load_polls()
    assert again[1:17] == [line("unchanged", name) for name in NAMES]
    assert again[17] == "Nothing changed: 0 created, 0 updated, 16 unchanged, 0 kept, 0 archived."
    assert await _state(world) == before
    assert await _events(admin) == events


async def test_archive_others_archives_every_other_round_which_can_be_restored(world: World) -> None:
    welcome = await world.event(name="Welcome")
    rehearsal = await world.live_event(name="Rehearsal")
    admin = await world.admin()
    voter = await world.participant()
    assert (await voter.vote(rehearsal.ballot.id, rehearsal.ballot.nominee_ids[0])).status_code == 201
    assert (await admin.close_event(rehearsal.id)).status_code == 200

    printed = await cli.load_polls(archive_others=True)
    assert printed[17:] == [
        line("archived", "Welcome"),
        line("archived", "Rehearsal"),
        "Done: 16 created, 0 updated, 0 unchanged, 0 kept, 2 archived.",
    ]
    events = await _events(admin)
    assert [e["name"] for e in events] == NAMES
    archived = await world.factory.fetch(
        "SELECT name, archived_at IS NOT NULL AS archived, position FROM events "
        "WHERE programme_id = :p AND id IN (:a, :b) ORDER BY position",
        p=world.programme_id,
        a=welcome.id,
        b=rehearsal.id,
    )
    assert [(r.name, r.archived, r.position) for r in archived] == [
        ("Welcome", True, 17),
        ("Rehearsal", True, 18),
    ]
    # Archived exactly as the console does it: audited, votes kept, hidden from voters.
    assert (
        await world.factory.fetchval(
            "SELECT count(*) FROM audit_log WHERE programme_id = :p AND action = 'event.archived' "
            "AND target_id IN (:a, :b)",
            p=world.programme_id,
            a=str(welcome.id),
            b=str(rehearsal.id),
        )
        == 2
    )
    assert await world.factory.fetchval("SELECT count(*) FROM votes WHERE event_id = :e", e=rehearsal.id) == 1
    assert (await voter.refresh())["event_count"] == 16

    # Restored from the console, a round comes back after the polls; a second load with
    # --archive-others archives it again and touches nothing else.
    assert (await admin.post(f"/events/{welcome.id}/unarchive")).status_code == 200
    assert [e["name"] for e in await _events(admin)] == [*NAMES, "Welcome"]
    again = await cli.load_polls(archive_others=True)
    assert again[1:] == [
        *(line("unchanged", name) for name in NAMES),
        line("archived", "Welcome"),
        "Done: 0 created, 0 updated, 16 unchanged, 0 kept, 1 archived.",
    ]
    assert [e["name"] for e in await _events(admin)] == NAMES


async def test_loading_is_refused_while_a_round_is_open(world: World) -> None:
    warm_up = await world.live_event(name="Warm-up")
    before = await _state(world)

    with pytest.raises(SystemExit, match=r"“Warm-up” is open\. Close it in Live control") as refused:
        await cli.load_polls(archive_others=True)
    assert "Nothing was changed." in str(refused.value)
    assert await _state(world) == before
    row = await world.factory.fetchrow("SELECT status, archived_at FROM events WHERE id = :e", e=warm_up.id)
    assert (row.status, row.archived_at) == ("open", None)

    assert (await (await world.admin()).close_event(warm_up.id)).status_code == 200
    printed = await cli.load_polls()
    assert printed[-1] == "Done: 16 created, 0 updated, 0 unchanged, 0 kept, 0 archived."


async def test_rounds_that_never_opened_follow_the_file_and_opened_ones_keep_their_options(
    world: World,
) -> None:
    second, fifth = NAMES[1], NAMES[4]
    draft = await world.event(name=second, nominees=2)
    played = await world.live_event(name=fifth, nominees=3)
    admin = await world.admin()
    voter = await world.participant()
    assert (await voter.vote(played.ballot.id, played.ballot.nominee_ids[2])).status_code == 201
    assert (await admin.close_event(played.id)).status_code == 200

    printed = await cli.load_polls()
    assert printed[2] == line("updated", second, " (organiser note, question and options)")
    assert printed[5] == line(KEPT, fifth, " (organiser note updated)")
    assert printed[-1] == "Done: 14 created, 1 updated, 0 unchanged, 1 kept, 0 archived."

    events = await _events(admin)
    assert [e["name"] for e in events] == NAMES
    by_name = {e["name"]: e for e in events}
    # The draft is the same round with the file's ballot in place of its own.
    updated = by_name[second]
    assert (updated["id"], updated["round"]) == (str(draft.id), 2)
    assert _as_file(updated) == _expected(POLLS[1])
    assert await world.factory.fetchval("SELECT count(*) FROM ballots WHERE id = :b", b=draft.ballot.id) == 0
    # The round that opened keeps its frozen ballot and votes; only the note is new.
    kept = by_name[fifth]
    assert (kept["id"], kept["round"], kept["status"]) == (str(played.id), 5, "closed")
    assert kept["organiser_note"] == POLLS[4]["organiser_note"]
    [ballot] = kept["ballots"]
    assert (ballot["id"], ballot["title"], ballot["vote_count"]) == (
        str(played.ballot.id),
        f"{fifth} ballot 1",
        1,
    )
    assert [n["id"] for n in ballot["nominees"]] == [str(n) for n in played.ballot.nominee_ids]
    actions = await world.factory.fetch(
        "SELECT action, target_id FROM audit_log WHERE programme_id = :p AND target_id IN (:a, :b) "
        "AND details->>'via' = 'load-polls' ORDER BY action",
        p=world.programme_id,
        a=str(draft.id),
        b=str(played.id),
    )
    assert [(a.action, a.target_id) for a in actions] == [
        ("event.presentation_corrected", str(played.id)),
        ("event.updated", str(draft.id)),
    ]

    again = await cli.load_polls()
    assert again[2] == line("unchanged", second)
    assert again[5] == line(KEPT, fifth)
    assert again[-1] == "Nothing changed: 0 created, 0 updated, 15 unchanged, 1 kept, 0 archived."


async def test_rounds_from_the_first_release_lose_the_id_in_front_of_their_names(world: World) -> None:
    # The first release named the rounds "P04 — TechSara Placement League"; one has had votes.
    played = await world.live_event(name=f"P05 — {NAMES[4]}", nominees=3)
    draft = await world.event(name=f"P04 — {NAMES[3]}", nominees=2)
    admin = await world.admin()
    voter = await world.participant()
    assert (await voter.vote(played.ballot.id, played.ballot.nominee_ids[0])).status_code == 201
    assert (await admin.close_event(played.id)).status_code == 200

    printed = await cli.load_polls()
    assert printed[4] == line("updated", NAMES[3], " (name, organiser note, question and options)")
    assert printed[5] == line(KEPT, NAMES[4], " (name and organiser note updated)")
    assert printed[-1] == "Done: 14 created, 1 updated, 0 unchanged, 1 kept, 0 archived."

    events = await _events(admin)
    assert [e["name"] for e in events] == NAMES
    by_name = {e["name"]: e for e in events}
    # The same rounds, renamed in place: the one that opened keeps its ballot and its vote.
    assert by_name[NAMES[3]]["id"] == str(draft.id)
    kept = by_name[NAMES[4]]
    assert (kept["id"], kept["status"], kept["ballots"][0]["vote_count"]) == (str(played.id), "closed", 1)
    renamed = await world.factory.fetch(
        "SELECT details->'name' AS name FROM audit_log WHERE target_id IN (:a, :b) "
        "AND details->>'via' = 'load-polls' AND details ? 'name' AND action <> 'event.created'",
        a=str(draft.id),
        b=str(played.id),
    )
    assert sorted(r.name["from"] for r in renamed) == [f"P04 — {NAMES[3]}", f"P05 — {NAMES[4]}"]
    assert (await cli.load_polls())[-1].startswith("Nothing changed:")


async def test_a_file_of_your_own_is_checked_before_anything_changes(world: World, tmp_path: Path) -> None:
    before = await _state(world)
    poll = {
        "name": "Quiz",
        "organiser_note": "",
        "ballot": {"kind": "mcq", "title": "Pick one", "options": [{"name": "Only"}]},
    }
    path = tmp_path / "polls.json"
    path.write_text(json.dumps({"polls": [poll]}), encoding="utf-8")
    with pytest.raises(SystemExit, match="needs at least two options"):
        await cli.load_polls(str(path))
    poll["ballot"]["options"] = [{"name": "Yes"}, {"name": "No"}]
    path.write_text(json.dumps({"polls": [poll, poll]}), encoding="utf-8")
    with pytest.raises(SystemExit, match="two polls are named 'Quiz'"):
        await cli.load_polls(str(path))
    individual = {
        "name": "Star",
        "ballot": {"kind": "individual", "title": "Who?", "options": [{"name": "A", "members": ["B"]}]},
    }
    path.write_text(json.dumps({"polls": [individual]}), encoding="utf-8")
    with pytest.raises(SystemExit, match="only the options of a team poll list members"):
        await cli.load_polls(str(path))
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(SystemExit, match="is not valid JSON"):
        await cli.load_polls(str(path))
    with pytest.raises(SystemExit, match="Cannot read"):
        await cli.load_polls(str(tmp_path / "missing.json"))
    assert await _state(world) == before

    path.write_text(json.dumps({"polls": [poll]}), encoding="utf-8")
    printed = await cli.load_polls(str(path))
    assert printed[1:] == [
        line("created", "Quiz"),
        "Done: 1 created, 0 updated, 0 unchanged, 0 kept, 0 archived.",
    ]


async def test_organiser_note_is_editable_after_freeze_and_never_reaches_participants(world: World) -> None:
    admin = await world.admin()
    note = "When to show: after the quiz.\nRead the question aloud first."
    created = await admin.post(
        "/events",
        {"name": "Quiz", "ballot_kind": "mcq", "ballot_title": "Pick one", "organiser_note": note},
    )
    assert created.status_code == 201, created.text
    event = created.json()["event"]
    assert event["organiser_note"] == note
    ballot_id = event["ballots"][0]["id"]
    for option in ("Yes", "No"):
        resp = await admin.post(f"/ballots/{ballot_id}/nominees", {"name": option})
        assert resp.status_code == 201, resp.text
    assert (await admin.get(f"/events/{event['id']}")).json()["event"]["organiser_note"] == note
    assert [e["organiser_note"] for e in await _events(admin)] == [note]
    live = (await admin.get("/live")).json()
    assert live["next_event"]["organiser_note"] == note
    assert [e["organiser_note"] for e in live["events"]] == [note]

    assert (await admin.open_event(uuid.UUID(event["id"]))).status_code == 200
    live = (await admin.get("/live")).json()
    assert live["current_event"]["organiser_note"] == note

    # Editable after the round froze, like the description; line endings are normalised.
    version = (await admin.get(f"/events/{event['id']}")).json()["event"]["version"]
    patched = await admin.patch(
        f"/events/{event['id']}", {"version": version, "organiser_note": "Updated\r\nsecond line"}
    )
    assert patched.status_code == 200, patched.text
    assert patched.json()["event"]["organiser_note"] == "Updated\nsecond line"
    assert patched.json()["event"]["frozen"] is True
    entry = await world.factory.fetchrow(
        "SELECT details FROM audit_log WHERE programme_id = :p AND action = 'event.presentation_corrected'",
        p=world.programme_id,
    )
    assert entry.details == {"organiser_note": {"from": note, "to": "Updated\nsecond line"}}
    version = patched.json()["event"]["version"]
    for bad in ("x" * 2001, "bell\x07"):
        assert_error(
            await admin.patch(f"/events/{event['id']}", {"version": version, "organiser_note": bad}),
            422,
            "VALIDATION_FAILED",
        )
    too_long = await admin.post("/events", {"name": "Too long", "organiser_note": "x" * 2001})
    assert_error(too_long, 422, "VALIDATION_FAILED")

    # Participants (and the preview of what they see) never get it.
    voter = await world.participant()
    snapshot = await voter.refresh()
    detail = (await voter.event(uuid.UUID(event["id"]))).json()
    preview = (await admin.get(f"/events/{event['id']}/preview")).json()
    for payload in (snapshot, detail, preview):
        text = dumps(payload)
        assert "organiser_note" not in text and "second line" not in text and "Read the question" not in text

    # Restarting from round 1 copies the note with the round.
    resp = await admin.post("/live/restart-programme", {"expected_live_version": await admin.live_version()})
    assert resp.status_code == 200, resp.text
    [copy] = await _events(admin)
    assert copy["id"] != event["id"] and copy["organiser_note"] == "Updated\nsecond line"


async def test_results_carry_the_ballot_description_and_no_votes_give_zero_percent(world: World) -> None:
    event = await world.event(kinds=("individual", "text"), nominees=3)
    people, question = event.ballots
    admin = await world.admin()
    description = "Audience Favourite. The official winner is decided by the highest bid."
    resp = await admin.patch(f"/ballots/{people.id}", {"description": description})
    assert resp.status_code == 200, resp.text
    assert (await admin.open_event(event.id)).status_code == 200

    live = (await admin.get("/live")).json()
    assert [r["description"] for r in live["current_event"]["results"]] == [description, ""]
    assert (await admin.close_event(event.id)).status_code == 200

    [item] = (await admin.get("/results", event_id=str(event.id))).json()["events"]
    shown, typed = item["ballots"]
    assert (shown["description"], shown["total_votes"], shown["leaders"], shown["tie"]) == (
        description,
        0,
        [],
        False,
    )
    assert [(n["votes"], n["percent"], n["leading"]) for n in shown["nominees"]] == [(0, 0.0, False)] * 3
    assert (typed["description"], typed["answer_count"], typed["answers"]) == ("", 0, [])
    every = (await admin.get("/results")).json()["events"]
    assert all("description" in b for e in every for b in e["ballots"])

    assert (await admin.post(f"/events/{event.id}/results/publish")).status_code == 200
    voter = await world.participant()
    results = (await voter.event(event.id)).json()["results"]
    assert [r["description"] for r in results] == [description, ""]
    assert [n["percent"] for n in results[0]["nominees"]] == [0.0, 0.0, 0.0]
    assert results[1] == {
        "ballot_id": str(question.id),
        "kind": "text",
        "description": "",
        "answer_count": 0,
    }
