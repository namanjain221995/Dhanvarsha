"""Question rounds: multiple-choice questions, typed answers, and rounds with no voting.

A multiple-choice (mcq) ballot is voted like a team ballot, with its answer options as
nominees. A typed answer (text ballot) is a vote row with answer_text and no nominee, so
it gets the same idempotency, finality and receipts as a vote. Organisers see the typed
answers; voters never see anyone else's.
"""

from __future__ import annotations

import csv
import io
import uuid

import pytest
from sqlalchemy.exc import DBAPIError

from helpers import AdminClient, World, assert_error, dumps, pg_error, receipt_of
from stagevote import cli

CHECK_VIOLATION = "23514"
VOTES_IMMUTABLE = "SV001"


async def _ballot_titles(admin: AdminClient, event_id: uuid.UUID) -> list[tuple[str, str]]:
    event = (await admin.get(f"/events/{event_id}")).json()["event"]
    return [(b["kind"], b["title"]) for b in event["ballots"]]


async def test_mcq_vote_records_the_chosen_option(world: World) -> None:
    event = await world.live_event(kinds=("mcq",), nominees=3)
    ballot = event.ballot
    voter = await world.participant()

    resp = await voter.vote(ballot.id, ballot.nominee_ids[1])
    assert resp.status_code == 201, resp.text
    receipt = resp.json()["receipt"]
    assert receipt["ballot_kind"] == "mcq"
    assert (receipt["nominee_id"], receipt["nominee_name"], receipt["answer"]) == (
        str(ballot.nominee_ids[1]),
        "Act 1.2",
        None,
    )
    assert (await voter.receipt(ballot.id)).json()["receipt"] == receipt
    detail = (await voter.event(event.id)).json()
    [shown] = detail["ballots"]
    assert shown["kind"] == "mcq" and len(shown["nominees"]) == 3 and shown["receipt"] == receipt
    # Answering twice is refused like voting twice.
    again = await voter.vote(ballot.id, ballot.nominee_ids[0])
    assert_error(again, 409, "ALREADY_VOTED")
    assert receipt_of(again) == receipt


async def test_typed_answer_is_trimmed_stored_and_receipted(world: World) -> None:
    event = await world.live_event(kinds=("text",))
    ballot = event.ballot
    assert ballot.nominee_ids == []
    voter = await world.participant()

    resp = await voter.answer(ballot.id, "  Blue whales\nand krill  \n")
    assert resp.status_code == 201, resp.text
    receipt = resp.json()["receipt"]
    assert receipt["ballot_kind"] == "text"
    assert (receipt["answer"], receipt["nominee_id"], receipt["nominee_name"]) == (
        "Blue whales\nand krill",
        None,
        None,
    )
    [row] = await world.factory.fetch(
        "SELECT nominee_id, answer_text, receipt_code FROM votes WHERE participant_id = :p", p=voter.info.id
    )
    assert (row.nominee_id, row.answer_text, row.receipt_code) == (
        None,
        "Blue whales\nand krill",
        receipt["receipt_code"],
    )

    # Every place a receipt appears carries the same shape.
    assert (await voter.receipt(ballot.id)).json()["receipt"] == receipt
    snapshot = await voter.refresh()
    assert snapshot["receipts"] == [receipt]
    [listed] = [e for e in snapshot["events"] if e["id"] == str(event.id)]
    assert listed["ballots"] == [
        {"id": str(ballot.id), "title": f"{event.name} ballot 1", "kind": "text", "voted": True}
    ]
    [shown] = (await voter.event(event.id)).json()["ballots"]
    assert (shown["kind"], shown["nominees"], shown["receipt"]) == ("text", [], receipt)

    # Live control counts the answers without showing them.
    live = (await (await world.admin()).get("/live")).json()
    [tally] = live["current_event"]["results"]
    assert (tally["kind"], tally["answer_count"], tally["nominees"]) == ("text", 1, [])
    assert "Blue whales" not in dumps(live)


async def test_answers_must_be_one_to_five_hundred_characters(world: World) -> None:
    event = await world.live_event(kinds=("text", "text"))
    first, second = event.ballots
    voter = await world.participant()

    for bad in ("", "   \n\t ", "x" * 501, "null\x00byte", "bell\x07"):
        error = assert_error(await voter.answer(first.id, bad), 422, "ANSWER_INVALID")
        assert error["message"] == "Type an answer of up to 500 characters."
    assert (
        await world.factory.fetchval("SELECT count(*) FROM votes WHERE participant_id = :p", p=voter.info.id)
        == 0
    )

    # The limit applies after trimming, and counts characters, not bytes.
    longest = "é" * 499 + "!"
    resp = await voter.answer(first.id, f"  {longest}  ")
    assert resp.status_code == 201, resp.text
    assert resp.json()["receipt"]["answer"] == longest
    resp = await voter.answer(second.id, "tab\tand\r\nnew line")
    assert resp.status_code == 201, resp.text


async def test_wrong_choice_for_the_ballot_kind_is_refused_both_ways(world: World) -> None:
    event = await world.live_event(kinds=("team", "text"))
    team, question = event.ballots
    voter = await world.participant()
    nominee = str(team.nominee_ids[0])

    for body in ({"nominee_id": nominee}, {}, {"nominee_id": nominee, "answer": "both"}):
        error = assert_error(await voter.submit(question.id, body), 422, "WRONG_CHOICE")
        assert error["message"] == "This question needs a typed answer."
    for body in ({"answer": "Team one"}, {}, {"nominee_id": nominee, "answer": ""}, {"nominee_id": None}):
        error = assert_error(await voter.submit(team.id, body), 422, "WRONG_CHOICE")
        assert error["message"] == "Choose one of the options."
    assert_error(await voter.submit(question.id, {"text": "typo"}), 422, "VALIDATION_FAILED")
    assert (
        await world.factory.fetchval("SELECT count(*) FROM votes WHERE participant_id = :p", p=voter.info.id)
        == 0
    )

    # The right field still works afterwards on both ballots.
    assert (await voter.vote(team.id, team.nominee_ids[0])).status_code == 201
    assert (await voter.answer(question.id, "Fine")).status_code == 201


async def test_typed_answer_replay_reuse_and_finality(world: World) -> None:
    event = await world.live_event(kinds=("text",))
    ballot = event.ballot
    voter = await world.participant()

    first = await voter.answer(ballot.id, "Seven", key="answer-1")
    assert first.status_code == 201, first.text
    receipt = first.json()["receipt"]

    # A lost response: the same request again (trimming makes it identical) replays.
    replay = await voter.answer(ballot.id, " Seven ", key="answer-1")
    assert replay.status_code == 200, replay.text
    assert replay.json() == {"receipt": receipt, "replayed": True}
    changed = await voter.answer(ballot.id, "Eight", key="answer-1")
    assert_error(changed, 409, "IDEMPOTENCY_KEY_REUSED")
    assert receipt_of(changed) == receipt
    again = await voter.answer(ballot.id, "Eight", key="answer-2")
    assert_error(again, 409, "ALREADY_VOTED")
    assert receipt_of(again) == receipt

    # After the round closes the original request still gets its receipt; new ones do not.
    admin = await world.admin()
    assert (await admin.close_event(event.id)).status_code == 200
    late = await voter.answer(ballot.id, "Seven", key="answer-1")
    assert late.status_code == 200 and late.json()["receipt"] == receipt
    other = await world.participant()
    assert_error(await other.answer(ballot.id, "Too late"), 409, "VOTING_CLOSED")
    assert await world.factory.fetchval("SELECT count(*) FROM votes WHERE ballot_id = :b", b=ballot.id) == 1


async def test_typed_answers_are_immutable_and_checked_by_the_database(world: World) -> None:
    event = await world.live_event(kinds=("text",))
    voter = await world.participant()
    assert (await voter.answer(event.ballot.id, "Original")).status_code == 201
    vote_id = await world.factory.fetchval("SELECT id FROM votes WHERE participant_id = :p", p=voter.info.id)

    with pytest.raises(DBAPIError) as caught:
        await world.factory.execute("UPDATE votes SET answer_text = 'Rewritten' WHERE id = :id", id=vote_id)
    assert pg_error(caught.value).sqlstate == VOTES_IMMUTABLE
    with pytest.raises(DBAPIError) as caught:
        # A copy of the row with an empty answer (and a new key) breaks ck_votes_choice.
        await world.factory.execute(
            "INSERT INTO votes (id, receipt_code, programme_id, event_id, ballot_id, nominee_id, answer_text, "
            "participant_id, session_id, idempotency_key, request_hash) "
            "SELECT gen_random_uuid(), 'R-TEST-0000', programme_id, event_id, gen_random_uuid(), NULL, '', "
            "participant_id, session_id, 'other', request_hash FROM votes WHERE id = :id",
            id=vote_id,
        )
    assert pg_error(caught.value).sqlstate == CHECK_VIOLATION
    assert (
        await world.factory.fetchval("SELECT answer_text FROM votes WHERE id = :id", id=vote_id) == "Original"
    )


async def test_text_ballots_have_no_options_and_mcq_options_no_photo(world: World) -> None:
    admin = await world.admin()
    created = await admin.post("/events", {"name": "Quiz", "ballot_kind": "text"})
    assert created.status_code == 201, created.text
    [question] = created.json()["event"]["ballots"]
    assert (question["kind"], question["title"], question["nominees"]) == ("text", "Your question here", [])
    assert_error(
        await admin.post(f"/ballots/{question['id']}/nominees", {"name": "An option"}),
        409,
        "TEXT_QUESTION_HAS_NO_OPTIONS",
    )

    event_id = uuid.UUID(created.json()["event"]["id"])
    resp = await admin.post(f"/events/{event_id}/ballots", {"title": "Best team", "kind": "team"})
    assert resp.status_code == 201, resp.text
    ballot_id = resp.json()["id"]
    resp = await admin.post(f"/ballots/{ballot_id}/nominees", {"name": "Lantern Lab", "members": ["Asha"]})
    assert resp.status_code == 201, resp.text
    nominee_id = resp.json()["id"]

    # A ballot with options cannot become a typed-answer question...
    assert_error(
        await admin.patch(f"/ballots/{ballot_id}", {"kind": "text"}), 409, "TEXT_QUESTION_HAS_NO_OPTIONS"
    )
    # ...but may become a multiple-choice question, whose options have no members or photo.
    assert (await admin.patch(f"/ballots/{ballot_id}", {"kind": "mcq"})).status_code == 200
    photo = str(uuid.uuid4())
    assert_error(
        await admin.post(f"/ballots/{ballot_id}/nominees", {"name": "Lunar", "photo_asset_id": photo}),
        409,
        "OPTION_HAS_NO_PHOTO",
    )
    assert_error(
        await admin.patch(f"/nominees/{nominee_id}", {"photo_asset_id": photo}), 409, "OPTION_HAS_NO_PHOTO"
    )
    resp = await admin.post(f"/ballots/{ballot_id}/nominees", {"name": "Lunar", "members": ["Ravi", "Meena"]})
    assert resp.status_code == 201, resp.text
    resp = await admin.patch(f"/nominees/{nominee_id}", {"members": ["Asha", "Dev"]})
    assert resp.status_code == 200, resp.text
    ballot = next(
        b for b in (await admin.get(f"/events/{event_id}")).json()["event"]["ballots"] if b["id"] == ballot_id
    )
    assert [(n["name"], n["members"]) for n in ballot["nominees"]] == [("Lantern Lab", []), ("Lunar", [])]

    # Once its options are removed it may become a typed-answer question.
    for nominee in ballot["nominees"]:
        assert (await admin.delete(f"/nominees/{nominee['id']}")).status_code == 200
    assert (await admin.patch(f"/ballots/{ballot_id}", {"kind": "text"})).status_code == 200

    # Questions may be longer than vote titles.
    question_title = "Which " + "very " * 36 + "long question?"
    assert len(question_title) == 200
    assert (await admin.patch(f"/ballots/{ballot_id}", {"title": question_title})).status_code == 200
    assert_error(await admin.patch(f"/ballots/{ballot_id}", {"title": "x" * 201}), 422, "VALIDATION_FAILED")
    assert await _ballot_titles(admin, event_id) == [
        ("text", "Your question here"),
        ("text", question_title),
    ]

    # The kind is fixed once voting opens.
    assert (await admin.open_event(event_id)).status_code == 200
    assert_error(await admin.patch(f"/ballots/{ballot_id}", {"kind": "mcq"}), 409, "BALLOT_FROZEN")


async def test_new_events_get_a_ballot_of_the_chosen_kind_or_none(world: World) -> None:
    admin = await world.admin()
    titles = {}
    for kind in ("team", "individual", "mcq", "text"):
        resp = await admin.post("/events", {"name": f"A {kind} round", "ballot_kind": kind})
        assert resp.status_code == 201, resp.text
        [ballot] = resp.json()["event"]["ballots"]
        assert ballot["kind"] == kind
        titles[kind] = ballot["title"]
    assert titles == {
        "team": "Your vote",
        "individual": "Your vote",
        "mcq": "Your question here",
        "text": "Your question here",
    }
    resp = await admin.post("/events", {"name": "Named", "ballot_kind": "mcq", "ballot_title": "Pick one?"})
    assert resp.json()["event"]["ballots"][0]["title"] == "Pick one?"
    resp = await admin.post("/events", {"name": "Just a show", "ballot_kind": "none"})
    assert resp.status_code == 201, resp.text
    assert resp.json()["event"]["ballots"] == []


async def test_a_round_with_no_ballots_opens_and_shows_no_voting(world: World) -> None:
    admin = await world.admin()
    # Deleting the last ballot of an upcoming event is allowed.
    event = await world.event(nominees=2)
    assert (await admin.delete(f"/ballots/{event.ballot.id}")).status_code == 200
    assert (await admin.get(f"/events/{event.id}")).json()["event"]["ballots"] == []
    preview = (await admin.get(f"/events/{event.id}/preview")).json()
    assert preview["ballots"] == []

    assert (await admin.open_event(event.id)).status_code == 200
    voter = await world.participant()
    snapshot = await voter.refresh()
    assert snapshot["phase"] == "live" and snapshot["live_event_id"] == str(event.id)
    [live] = [e for e in snapshot["events"] if e["id"] == str(event.id)]
    assert (live["status"], live["ballots"], live["ballot_count"]) == ("live", [], 0)
    detail = (await voter.event(event.id)).json()
    assert detail["event"]["status"] == "live" and detail["ballots"] == []

    assert (await admin.close_event(event.id)).status_code == 200
    assert (await admin.post(f"/events/{event.id}/results/publish")).status_code == 200
    assert (await voter.event(event.id)).json()["results"] == []


async def test_opening_checks_by_ballot_kind(world: World) -> None:
    admin = await world.admin()
    # A typed-answer question needs nothing before it opens.
    text_only = await world.event(kinds=("text",))
    assert (await admin.open_event(text_only.id)).status_code == 200
    assert (await admin.close_event(text_only.id)).status_code == 200

    # A multiple-choice question needs at least two answers, a vote at least one nominee.
    lacking = await world.event(name="Lacking", kinds=("team", "mcq", "text"), nominees=0)
    error = assert_error(await admin.open_event(lacking.id), 409, "EVENT_NOT_READY")
    assert (
        error["message"]
        == "Add nominees to: Lacking ballot 1. Add at least two answers to: Lacking ballot 2."
    )
    team, mcq, _ = lacking.ballots
    assert (await admin.post(f"/ballots/{team.id}/nominees", {"name": "Solo act"})).status_code == 201
    assert (await admin.post(f"/ballots/{mcq.id}/nominees", {"name": "Yes"})).status_code == 201
    error = assert_error(await admin.open_event(lacking.id), 409, "EVENT_NOT_READY")
    assert error["message"] == "Add at least two answers to: Lacking ballot 2."
    assert (await admin.post(f"/ballots/{mcq.id}/nominees", {"name": "No"})).status_code == 201
    assert (await admin.open_event(lacking.id)).status_code == 200


async def _answered_round(world: World) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID, list[uuid.UUID]]:
    """A closed round with an mcq and a text ballot: two voters, two typed answers."""
    event = await world.live_event(name="Quiz", kinds=("mcq", "text"), nominees=3)
    mcq, question = event.ballots
    first = await world.participant()
    second = await world.participant()
    for voter, typed in ((first, "First answer\nwith a second line"), (second, "=SUM(A1:A9)")):
        assert (await voter.vote(mcq.id, mcq.nominee_ids[0])).status_code == 201
        assert (await voter.answer(question.id, typed)).status_code == 201
    assert (await (await world.admin()).close_event(event.id)).status_code == 200
    return event.id, mcq.id, question.id, [first.info.id, second.info.id]


async def test_organiser_results_and_csv_include_typed_answers(world: World) -> None:
    event_id, mcq_id, question_id, voters = await _answered_round(world)
    admin = await world.admin()
    emails = [
        await world.factory.fetchval("SELECT email FROM participants WHERE id = :p", p=p) for p in voters
    ]

    [item] = (await admin.get("/results", event_id=str(event_id))).json()["events"]
    mcq, question = item["ballots"]
    assert (mcq["ballot_id"], mcq["kind"], mcq["total_votes"]) == (str(mcq_id), "mcq", 2)
    assert [(n["votes"], n["percent"]) for n in mcq["nominees"]] == [(2, 100.0), (0, 0.0), (0, 0.0)]
    assert (question["ballot_id"], question["kind"], question["answer_count"]) == (
        str(question_id),
        "text",
        2,
    )
    assert [(a["participant_name"], a["participant_email"], a["answer"]) for a in question["answers"]] == [
        ("Test Participant", emails[0], "First answer\nwith a second line"),
        ("Test Participant", emails[1], "=SUM(A1:A9)"),
    ]
    assert question["answers"][0]["created_at"] <= question["answers"][1]["created_at"]

    resp = await admin.get("/results/export.csv", event_id=str(event_id))
    assert resp.status_code == 200, resp.text
    rows = list(csv.reader(io.StringIO(resp.text)))
    blank = rows.index([])
    counts, answers = rows[:blank], rows[blank + 1 :]
    assert counts[0][:5] == ["round", "event", "event_status", "result_label", "ballot"]
    assert [r[7] for r in counts[1:]] == ["2", "0", "0"]  # the mcq options, like votes
    assert answers[0] == [
        "event",
        "ballot",
        "kind",
        "participant_name",
        "participant_email",
        "answer",
        "time",
    ]
    assert [r[:6] for r in answers[1:]] == [
        ["Quiz", "Quiz ballot 2", "text", "Test Participant", emails[0], "First answer\nwith a second line"],
        ["Quiz", "Quiz ballot 2", "text", "Test Participant", emails[1], "'=SUM(A1:A9)"],
    ]


async def test_published_results_never_show_typed_answers_to_voters(world: World) -> None:
    event_id, mcq_id, question_id, _ = await _answered_round(world)
    admin = await world.admin()
    assert (await admin.post(f"/events/{event_id}/results/publish")).status_code == 200

    voter = await world.participant()
    detail = (await voter.event(event_id)).json()
    mcq, question = detail["results"]
    assert (mcq["ballot_id"], mcq["kind"], mcq["total_votes"]) == (str(mcq_id), "mcq", 2)
    assert question == {"ballot_id": str(question_id), "kind": "text", "description": "", "answer_count": 2}
    text = dumps(detail)
    assert "First answer" not in text and "SUM(A1" not in text


async def test_reconcile_counts_typed_answers_separately(
    world: World, capsys: pytest.CaptureFixture[str]
) -> None:
    await _answered_round(world)
    slug = await world.factory.fetchval("SELECT slug FROM programmes WHERE id = :p", p=world.programme_id)
    capsys.readouterr()

    assert await cli.reconcile(slug) == 0
    out = capsys.readouterr().out
    lines = {
        line.split(":")[0].strip(): line.split(":")[-1].strip() for line in out.splitlines() if ":" in line
    }
    assert lines["votes stored"] == "4"
    assert lines["votes for nominees"] == "2"
    assert lines["sum of nominee tallies"] == "2"
    assert lines["typed answers"] == "2"
    assert out.splitlines()[-1] == "RECONCILED"
