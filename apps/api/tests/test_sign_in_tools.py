"""Operator tools that hand out sign-in links without email: `stagevote sign-in-link`,
`seed-loadtest` (the load tests' token file) and `seed-demo --activate-sample`."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from helpers import API, World, assert_error, secret_of
from stagevote import cli
from stagevote.config import get_settings
from stagevote.security import token_hash
from stagevote.seed import seed_demo, seed_loadtest

TOKEN = re.compile(r"[A-Za-z0-9_-]{43}")
LINK = re.compile(r"http://testserver/enter#token=([A-Za-z0-9_-]{43})")


async def test_sign_in_link_replaces_the_link_like_an_invitation_but_prints_it(
    world: World, capsys: pytest.CaptureFixture[str]
) -> None:
    person = await world.participant(login=False, activated=False)
    assert (await (await world.admin()).post(f"/participants/{person.info.id}/invite")).status_code == 200
    job = await world.factory.fetchrow(
        "SELECT id, secret_ciphertext FROM email_jobs WHERE participant_id = :p", p=person.info.id
    )
    emailed = secret_of(job.secret_ciphertext)
    assert (await person.enter(emailed)).status_code == 200
    capsys.readouterr()

    printed = await cli.sign_in_link(person.info.email.upper())
    out, err = capsys.readouterr()
    assert out == printed + "\n"
    match = LINK.fullmatch(printed)
    assert match is not None
    token = match.group(1)
    assert token != emailed and token not in err and "1 session(s) signed out" in err

    # Exactly what a new invitation does, without the email.
    row = await world.factory.fetchrow("SELECT * FROM participants WHERE id = :p", p=person.info.id)
    assert row.link_token_hash == token_hash(token) and row.link_device_hash is None
    old_job = await world.factory.fetchrow(
        "SELECT status, secret_ciphertext FROM email_jobs WHERE id = :j", j=job.id
    )
    assert (old_job.status, old_job.secret_ciphertext) == ("superseded", None)
    assert (
        await world.factory.fetchval(
            "SELECT count(*) FROM email_jobs WHERE participant_id = :p", p=person.info.id
        )
        == 1
    )
    error = assert_error(await person.snapshot(), 401, "SESSION_REVOKED")
    assert error["details"]["reason"] == "credentials_reissued"
    assert (
        await world.factory.fetchval(
            "SELECT details->>'via' FROM audit_log WHERE action = 'participant.link_issued' AND target_id = :t",
            t=str(person.info.id),
        )
        == "cli"
    )
    assert_error(await person.enter(emailed), 401, "LINK_INVALID")
    assert (await world.browser(person.info).enter(token)).status_code == 200


async def test_sign_in_link_refuses_production_unknown_and_disabled_people(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    person = await world.participant(login=False)
    with pytest.raises(SystemExit, match="No participant"):
        await cli.sign_in_link("nobody@example.com")
    await world.factory.execute(
        "UPDATE participants SET disabled_at = clock_timestamp() WHERE id = :p", p=person.info.id
    )
    with pytest.raises(SystemExit, match="disabled"):
        await cli.sign_in_link(person.info.email)
    monkeypatch.setattr(get_settings(), "app_env", "production")
    with pytest.raises(SystemExit, match="production"):
        await cli.sign_in_link(person.info.email)
    with pytest.raises(SystemExit, match="production"):
        await seed_loadtest(1, 1)
    # The person's link was never touched by any of these.
    assert await world.factory.fetchval(
        "SELECT link_token_hash FROM participants WHERE id = :p", p=person.info.id
    ) == token_hash(person.info.link_token or "")


async def test_seed_loadtest_writes_one_working_token_per_account(
    world: World, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    accounts = tmp_path / "accounts.txt"
    monkeypatch.setenv("LOADTEST_ACCOUNTS_FILE", str(accounts))
    await seed_loadtest(3, 1)
    out = capsys.readouterr().out
    assert "password" not in out.lower()

    tokens = accounts.read_text().splitlines()
    assert len(tokens) == len(set(tokens)) == 3 and all(TOKEN.fullmatch(t) for t in tokens)
    programme = await world.factory.fetchrow("SELECT id, slug FROM programmes WHERE is_active")
    assert programme.slug.startswith("loadtest-")
    rows = await world.factory.fetch(
        "SELECT link_token_hash, link_device_hash, password_hash, activated_at, synthetic "
        "FROM participants WHERE programme_id = :p",
        p=programme.id,
    )
    # Only the hashes are stored: the file is the one place the tokens exist.
    assert {r.link_token_hash for r in rows} == {token_hash(t) for t in tokens}
    assert all(r.synthetic and r.password_hash is None and r.link_device_hash is None for r in rows)

    # Each token signs in one browser.
    first = world.http()
    assert (await first.post(f"{API}/auth/link", json={"token": tokens[0]})).status_code == 200
    assert_error(await world.http().post(f"{API}/auth/link", json={"token": tokens[0]}), 403, "LINK_IN_USE")


async def test_seed_demo_sample_accounts_get_links_instead_of_a_password(
    world: World, capsys: pytest.CaptureFixture[str]
) -> None:
    await seed_demo(3, 2)
    out = capsys.readouterr().out
    assert "password" not in out.lower()
    links = dict(re.findall(r"(guest\d{3}@example\.com)\s+(\S+)", out))
    assert sorted(links) == ["guest001@example.com", "guest002@example.com"]
    tokens = {address: LINK.fullmatch(link) for address, link in links.items()}
    assert all(tokens.values())
    for address, match in tokens.items():
        assert match is not None
        stored = await world.factory.fetchval(
            "SELECT link_token_hash FROM participants WHERE programme_id = :p AND email = :e",
            p=world.programme_id,
            e=address,
        )
        assert stored == token_hash(match.group(1))
    assert (
        await world.factory.fetchval(
            "SELECT link_token_hash FROM participants WHERE programme_id = :p AND email = 'guest003@example.com'",
            p=world.programme_id,
        )
        is None
    )
    sample = tokens["guest001@example.com"]
    assert sample is not None
    assert (await world.http().post(f"{API}/auth/link", json={"token": sample.group(1)})).status_code == 200
