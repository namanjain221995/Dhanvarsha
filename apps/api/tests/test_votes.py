"""The vote transaction: one row per ballot, idempotent retries and durable receipts."""

from __future__ import annotations

import asyncio
import re
import uuid

import httpx
import pytest

from helpers import (
    API,
    ParticipantClient,
    World,
    assert_error,
    error_code,
    receipt_of,
)
from stagevote.services import voting

RECEIPT = re.compile(r"R-[ABCDEFGHJKMNPQRSTUVWXYZ2-9]{4}-[ABCDEFGHJKMNPQRSTUVWXYZ2-9]{4}")


async def votes_of(world: World, participant_id: uuid.UUID) -> list:
    return await world.factory.fetch(
        "SELECT * FROM votes WHERE participant_id = :p ORDER BY created_at", p=participant_id
    )


async def test_accepted_vote_persists_exactly_one_row_with_its_receipt(world: World) -> None:
    event = await world.live_event(ballots=2)
    voter = await world.participant()
    ballot = event.ballot
    session_id = await world.factory.fetchval(
        "SELECT id FROM participant_sessions WHERE participant_id = :p AND revoked_at IS NULL",
        p=voter.info.id,
    )

    resp = await voter.vote(ballot.id, ballot.nominee_ids[1], key="first-attempt")
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["replayed"] is False
    receipt = body["receipt"]
    assert RECEIPT.fullmatch(receipt["receipt_code"])
    assert (receipt["ballot_id"], receipt["nominee_id"], receipt["event_id"]) == (
        str(ballot.id),
        str(ballot.nominee_ids[1]),
        str(event.id),
    )

    [row] = await votes_of(world, voter.info.id)
    assert (row.receipt_code, row.nominee_id, row.session_id, row.idempotency_key) == (
        receipt["receipt_code"],
        ballot.nominee_ids[1],
        session_id,
        "first-attempt",
    )
    tally = await world.factory.fetch(
        "SELECT payload FROM outbox_events WHERE programme_id = :p AND topic = 'tally'", p=world.programme_id
    )
    assert [t.payload for t in tally] == [{"e": str(event.id), "b": str(ballot.id)}]

    assert (await voter.receipt(ballot.id)).json()["receipt"] == receipt
    snapshot = (await voter.snapshot()).json()
    assert snapshot["receipts"] == [receipt]
    [shown] = [e for e in snapshot["events"] if e["id"] == str(event.id)]
    assert [b["voted"] for b in shown["ballots"]] == [True, False]


async def test_simultaneous_duplicate_submissions_store_one_vote(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Twelve concurrent submissions for one ballot: one winner, consistent answers.

    All twelve transactions are held until each has passed the application-level
    "already voted?" check, so every one of them really attempts the INSERT and the
    database's UNIQUE(participant_id, ballot_id) has to arbitrate.
    """
    original_existing = voting._existing
    barrier = asyncio.Barrier(12)
    passed_check: set[asyncio.Task[object] | None] = set()
    insert_attempts = 0
    original_code = voting.new_receipt_code

    async def existing_then_wait(*args: object) -> list[object]:
        rows = await original_existing(*args)  # type: ignore[arg-type]
        task = asyncio.current_task()
        if task not in passed_check:
            passed_check.add(task)
            await asyncio.wait_for(barrier.wait(), timeout=10)
        return rows

    def counting_code() -> str:
        nonlocal insert_attempts
        insert_attempts += 1
        return original_code()

    monkeypatch.setattr(voting, "_existing", existing_then_wait)
    monkeypatch.setattr(voting, "new_receipt_code", counting_code)
    event = await world.live_event()
    voter = await world.participant()
    ballot = event.ballot
    # Separate HTTP clients (same session cookie) so every request has its own connection.
    clients = [voter.http] + [world.http() for _ in range(3)]
    for client in clients[1:]:
        client.cookies.set("sv_session", voter.token or "")

    plan: list[tuple[str, uuid.UUID]] = []
    plan += [("shared-key", ballot.nominee_ids[0])] * 4  # identical retries
    plan += [(f"other-key-{i}", ballot.nominee_ids[0]) for i in range(4)]  # double clicks with new keys
    plan += [(f"switch-key-{i}", ballot.nominee_ids[1]) for i in range(4)]  # attempts to change the choice

    async def submit(i: int, key: str, nominee: uuid.UUID) -> httpx.Response:
        sender = ParticipantClient(clients[i % len(clients)], voter.info)
        sender.csrf = voter.csrf
        return await sender.vote(ballot.id, nominee, key=key)

    responses = await asyncio.gather(*(submit(i, k, n) for i, (k, n) in enumerate(plan)))

    created = [r for r in responses if r.status_code == 201]
    assert len(created) == 1, [(r.status_code, r.text) for r in responses]
    winner = created[0].json()["receipt"]
    winner_index = responses.index(created[0])
    winner_key, winner_nominee = plan[winner_index]
    for (key, nominee), resp in zip(plan, responses, strict=True):
        if resp is created[0]:
            continue
        if key == winner_key and nominee == winner_nominee:
            assert resp.status_code == 200 and resp.json()["replayed"] is True, resp.text
        else:
            assert_error(resp, 409, "ALREADY_VOTED")
        assert receipt_of(resp) == winner

    [row] = await votes_of(world, voter.info.id)
    assert (row.receipt_code, str(row.nominee_id)) == (winner["receipt_code"], winner["nominee_id"])
    assert insert_attempts == len(plan)  # every request reached the INSERT


async def test_idempotency_key_rules(world: World) -> None:
    event = await world.live_event(ballots=2)
    voter = await world.participant()
    first, second = event.ballots

    original = await voter.vote(first.id, first.nominee_ids[0], key="key-A")
    assert original.status_code == 201
    receipt = original.json()["receipt"]

    # Same key, same payload: the original receipt is replayed.
    replay = await voter.vote(first.id, first.nominee_ids[0], key="key-A")
    assert replay.status_code == 200 and replay.json() == {"receipt": receipt, "replayed": True}
    # Same key, different nominee: refused, nothing changes.
    reused = await voter.vote(first.id, first.nominee_ids[1], key="key-A")
    assert receipt_of(assert_error_response(reused, 409, "IDEMPOTENCY_KEY_REUSED")) == receipt
    # Different key, other nominee: votes are final.
    again = await voter.vote(first.id, first.nominee_ids[2], key="key-B")
    assert receipt_of(assert_error_response(again, 409, "ALREADY_VOTED")) == receipt
    # A key belongs to one request: reusing it for another ballot is refused too.
    assert_error(
        await voter.vote(second.id, second.nominee_ids[0], key="key-A"), 409, "IDEMPOTENCY_KEY_REUSED"
    )
    # Missing or malformed keys are rejected before anything is stored.
    assert_error(
        await voter.vote(second.id, second.nominee_ids[0], send_key=False), 400, "IDEMPOTENCY_KEY_REQUIRED"
    )
    assert_error(
        await voter.vote(second.id, second.nominee_ids[0], key="x" * 81), 400, "IDEMPOTENCY_KEY_REQUIRED"
    )
    assert_error(
        await voter.vote(second.id, second.nominee_ids[0], key="   "), 400, "IDEMPOTENCY_KEY_REQUIRED"
    )

    rows = await votes_of(world, voter.info.id)
    assert [(r.ballot_id, r.nominee_id) for r in rows] == [(first.id, first.nominee_ids[0])]
    resp = await voter.vote(second.id, second.nominee_ids[0], key="key-C")
    assert resp.status_code == 201
    assert len(await votes_of(world, voter.info.id)) == 2


def assert_error_response(resp: httpx.Response, status: int, code: str) -> httpx.Response:
    assert_error(resp, status, code)
    return resp


class DropVoteResponse(httpx.AsyncBaseTransport):
    """Delivers the request to the server, then loses the response (like a dropped connection)."""

    def __init__(self, inner: httpx.AsyncBaseTransport) -> None:
        self.inner = inner
        self.dropped = 0

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        response = await self.inner.handle_async_request(request)
        if request.method == "POST" and request.url.path.endswith("/vote") and not self.dropped:
            await response.aread()  # the server has finished and committed
            self.dropped += 1
            raise httpx.ReadError("connection reset before the response arrived", request=request)
        return response


async def test_lost_response_retry_with_same_key_returns_the_committed_receipt(world: World) -> None:
    event = await world.live_event()
    voter = await world.participant()
    transport = DropVoteResponse(httpx.ASGITransport(app=world.app, client=(world.ip, 51000)))
    flaky = ParticipantClient(world.http(transport=transport), voter.info)
    flaky.http.cookies.set("sv_session", voter.token or "")
    flaky.csrf = voter.csrf
    ballot = event.ballot

    try:
        await flaky.vote(ballot.id, ballot.nominee_ids[2], key="phone-key-1")
        raise AssertionError("the response should have been lost")
    except httpx.ReadError:
        pass
    assert transport.dropped == 1
    [row] = await votes_of(world, voter.info.id)  # the server did commit

    retry = await flaky.vote(ballot.id, ballot.nominee_ids[2], key="phone-key-1")
    assert retry.status_code == 200, retry.text
    assert retry.json()["replayed"] is True
    assert retry.json()["receipt"]["receipt_code"] == row.receipt_code
    # Even a client that also lost its key learns the outcome instead of double voting.
    fresh = await flaky.vote(ballot.id, ballot.nominee_ids[0], key="phone-key-2")
    assert receipt_of(assert_error_response(fresh, 409, "ALREADY_VOTED"))["receipt_code"] == row.receipt_code
    assert len(await votes_of(world, voter.info.id)) == 1


async def test_receipts_survive_close_logout_and_new_login(world: World) -> None:
    event = await world.live_event(ballots=2)
    voter = await world.participant()
    first, second = event.ballots
    original = await voter.vote(first.id, first.nominee_ids[1], key="before-close")
    assert original.status_code == 201
    receipt = original.json()["receipt"]

    admin = await world.admin()
    resp = await admin.close_event(event.id)
    assert resp.status_code == 200, resp.text

    assert (await voter.receipt(first.id)).json() == {"receipt": receipt}
    replay = await voter.vote(first.id, first.nominee_ids[1], key="before-close")
    assert replay.status_code == 200 and replay.json()["receipt"] == receipt
    assert_error(await voter.vote(second.id, second.nominee_ids[0]), 409, "VOTING_CLOSED")
    assert_error(await voter.receipt(second.id), 404, "NO_VOTE")
    snapshot = (await voter.snapshot()).json()
    assert snapshot["receipts"] == [receipt]

    assert (await voter.logout()).status_code == 200
    assert_error(await voter.receipt(first.id), 401, "UNAUTHENTICATED")
    resp = await voter.enter()  # the same browser opens the invitation link again
    assert resp.status_code == 200 and resp.json()["reused_session"] is False
    assert (await voter.receipt(first.id)).json() == {"receipt": receipt}
    replay = await voter.vote(first.id, first.nominee_ids[1], key="before-close")
    assert replay.status_code == 200 and replay.json()["receipt"] == receipt
    assert len(await votes_of(world, voter.info.id)) == 1
    assert error_code(await voter.vote(second.id, second.nominee_ids[0])) == "VOTING_CLOSED"


async def test_vote_requires_a_json_nominee(world: World) -> None:
    event = await world.live_event()
    voter = await world.participant()
    resp = await voter.http.post(
        f"{API}/me/ballots/{event.ballot.id}/vote",
        json={"nominee_id": "not-a-uuid"},
        headers={"Idempotency-Key": "k", "X-CSRF-Token": voter.csrf or ""},
    )
    assert_error(resp, 422, "VALIDATION_FAILED")
    resp = await voter.http.post(
        f"{API}/me/ballots/{event.ballot.id}/vote",
        json={"nominee_id": str(event.ballot.nominee_ids[0]), "weight": 5},
        headers={"Idempotency-Key": "k2", "X-CSRF-Token": voter.csrf or ""},
    )
    assert_error(resp, 422, "VALIDATION_FAILED")
    assert (
        await world.factory.fetchval("SELECT count(*) FROM votes WHERE participant_id = :p", p=voter.info.id)
        == 0
    )
