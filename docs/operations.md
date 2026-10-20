# Operator runbook

For the people who run StageVote before and during a live programme. Commands assume the
repository root. Production uses `compose.prod.yaml` (add `-f compose.gmail.yaml` for the
Gmail API provider); development uses `compose.yaml`.

## Development and production differ on purpose

| | Development (`compose.yaml`) | Production (`compose.prod.yaml`) |
|---|---|---|
| URL | `http://localhost:<STAGEVOTE_HTTP_PORT>` (bootstrap picks a free port, 3000 first) | `https://<SITE_DOMAIN>` with automatic certificates (ports 80/443) |
| Cookies | plain names, not `Secure` (HTTP on localhost) | `__Host-` names, `Secure`, `HttpOnly`, `SameSite=Lax` |
| Email | Mailpit test inbox on `http://localhost:8025` | `smtp` or `gmail_workspace`; Mailpit and plain-text SMTP are refused |
| Database | published on `127.0.0.1:55432` for tests | not published |
| API docs | `/api/v1/docs` | disabled |
| Demo data | `stagevote seed-demo` (opt-in) | refused |

## First production deployment

On AWS EC2, follow [deploy-ec2.md](deploy-ec2.md): `scripts/server-setup.sh` and
`scripts/deploy.sh` do the steps below and run the capacity tests on the server.

1. Point DNS for `SITE_DOMAIN` at the server; open ports 80 and 443.
2. `scripts/bootstrap.sh` to generate secrets, then edit `.env`:
   `APP_ENV=production`, `APP_BASE_URL=https://<domain>`, `SITE_DOMAIN`, `ACME_EMAIL`,
   `DATABASE_URL`/`DATABASE_MIGRATION_URL` with host `db`, the email settings
   ([email-setup.md](email-setup.md)), `MAIL_FROM` on your domain, `ADMIN_BOOTSTRAP_EMAIL`
   and a strong `ADMIN_BOOTSTRAP_PASSWORD`. Keep `.env` mode 0600; never commit it.
3. `docker compose -f compose.prod.yaml up -d --build`. The `migrate` service applies
   migrations, creates the default programme and the first admin, then exits.
4. Sign in at `/admin/login`, then **remove `ADMIN_BOOTSTRAP_PASSWORD` from `.env`**
   (reruns never reset an existing admin, but the secret should not linger). Add more
   organisers with `docker compose -f compose.prod.yaml run --rm migrate stagevote create-admin --email someone@org`.
5. `docker compose -f compose.prod.yaml exec api stagevote check-privileges` must report
   that every vote/audit modification is refused.

## One week before

- **Programme settings**: name, logo, accent, timezone, access window (opens/closes),
  countdown, help contact (shown on sign-in and in emails), preview of upcoming titles,
  publishing of next start times.
- **Rounds**: create events in order; each needs at least one ballot with nominees. Add a
  second named ballot only when a round really has two questions (for example *Best team*
  and *Best member*). Nominee names freeze when voting first opens.
- **Team members**: on a *team* ballot, list each team's members (one name per line or
  separated by commas, up to 30). Voters see them under the team name; the vote is for the
  whole team, never for one member. The list can still be corrected after voting opens, and
  every change is in the audit log.
- **Schedule** (optional): set planned windows and switch the run mode to *scheduled*.
  Windows must not overlap and must sit inside the access window.
- **Participants**: import emails (paste or CSV with `email` and optional `name`). Review
  the preview (invalid, duplicates, already imported) before importing.
- **Email**: run diagnostics, send a test to yourself, then invite everyone. Each
  invitation contains the participant's user ID (their email address) and password; the
  account works as soon as the email arrives. Watch the email page until every invitation
  is `accepted`; follow up on `failed` and `unknown`. Ask participants to keep the email.
- **Backups**: `scripts/backup.sh -f compose.prod.yaml` and practise a restore on a
  spare machine (see below).

## The day before

- Rehearse with a test round and two or three test participants on real phones over the
  venue Wi-Fi and mobile data. Archive the test round afterwards (votes stay in the
  database but the round disappears from the programme).
- After a full rehearsal of the real rounds, use *Live control › Start again › Restart from
  round 1*. It closes any open round, archives every round with its votes (votes are never
  deleted; they stop counting and are hidden from participants and results) and creates
  identical fresh copies, all upcoming, in the same order. Participants stay signed in and
  can vote again. Planned times already in the past are cleared.
- Check: *Live control* shows the worker as healthy and the live channel connected;
  `curl -fsS https://<domain>/api/v1/ready` returns `"status":"ok"`.
- Print the help-desk sheet: how to reset a session and resend an invitation
  (below).

## During the programme

**Live control** is the only page an operator needs.

- *Open event* opens a round with a deadline (default: when programme access closes).
- *Start next event* closes the current round and opens the next in one step.
- *Close voting* ends the current round immediately. Accepted votes are final; closed
  rounds never reopen.
- *Change deadline* extends or shortens the live round.
- In scheduled mode, any of these actions turns on **manual override**: the schedule
  stops acting until you press *Resume schedule*, which first shows what will happen.
- If a button reports *Another organiser or the schedule changed the live state*, the
  page refreshes itself; check the new state before acting again.

### Help-desk answers

| Participant says | Do this |
|---|---|
| "It says my account is signed in on another browser" | Participants › find them › *Reset session*. They can then sign in on the device in hand. |
| "I lost the email / my password doesn't work" | Participants › find them › *Resend invitation*. A new email arrives with a new password; the old password stops working and any open session is signed out. (There is no self-service reset by design.) |
| "I never got an invitation" | Check the address on the participant row and the email page status; fix the address if needed and *Send invitation*. Ask them to look in spam. |
| "Did my vote count?" | Their receipt is on the round page and in the lobby. Admins can see that a ballot was voted (completion), never the choice. |
| "The page says Reconnecting" | Usually venue Wi-Fi. Voting pauses until the phone confirms the latest state; the page recovers on its own. Switching to mobile data is fine; the session continues. |

## Email problems

- Run *Admin › Email › Run diagnostics*. Typical causes: wrong SMTP password
  (`smtp_auth_failed`), delegation not authorised or not yet propagated
  (`unauthorized_client`), wrong sender mailbox (`invalid_grant`).
- After a configuration fix, *Retry all failed* requeues failed invitations that still have
  their encrypted password copy. Older ones need a fresh invitation (new password).
- `unknown` means the provider may already have the message. Retry only if a duplicate is
  acceptable (the same password is resent).
- Sending pauses for 5 minutes after a configuration error; queued jobs wait, they are not
  lost.

## Reconnection and live-update problems

1. `curl -fsS https://<domain>/api/v1/ready` – database reachable and migrated; reports
   whether this API process's live listener is connected.
2. *Live control* shows the worker heartbeat; if it is stale:
   `docker compose -f compose.prod.yaml logs --tail=200 worker`, then
   `docker compose -f compose.prod.yaml restart worker`. Leases recover automatically;
   an email that was mid-send becomes `unknown` rather than being sent twice.
3. Votes do not depend on live updates. If notifications stop, phones still refresh every
   15–60 seconds and votes keep their exact deadline (enforced by the database clock).
4. API logs are JSON with a `request_id`; every error response carries the same id.

## Backups and restore

```sh
scripts/backup.sh -f compose.prod.yaml                 # database dump + media archive in backups/
scripts/restore.sh --yes-replace-database backups/stagevote-<stamp>.dump backups/media-<stamp>.tgz -- -f compose.prod.yaml
docker compose -f compose.prod.yaml run --rm migrate stagevote reconcile
```

- Back up before doors open, after the last round, and before any upgrade.
- Dumps contain participant emails and password hashes: keep them encrypted and on
  restricted storage.
- `restore.sh` is destructive: it stops the API and worker, drops and recreates the
  database, restores, reapplies migrations and grants, restarts, and checks privileges.
- `stagevote reconcile` compares stored votes with tallies and checks for duplicates and
  cross-ballot references.

## Secrets and rotation

| Secret | Rotation effect |
|---|---|
| `APP_SECRET_KEY` | CSRF tokens change; open pages need a reload. Sessions stay valid. |
| `INVITATION_ENCRYPTION_KEY` | Queued invitation emails can no longer be decrypted (they fail with `payload_unreadable`); send those invitations again. |
| DB passwords | Change in PostgreSQL (`ALTER ROLE`) and in `.env` together, then restart. |
| Google service-account key | Create the new key, replace `secrets/google-service-account.json`, restart api and worker, delete the old key in Google Cloud. |

## Capacity

Measured locally (see [verification.md](verification.md)): 500 simultaneous
participants with live sockets, three rounds with a 10-second vote burst each, on two
API processes. Scale by raising `API_WORKERS` (each process holds up to
`DB_POOL_SIZE + DB_MAX_OVERFLOW` connections; keep the total below PostgreSQL's
`max_connections`). Venue Wi-Fi is usually the real bottleneck: test it.

## After the programme

- Publish results per round (*Results › Publish*) if participants should see them.
- Export aggregate CSV.
- Back up, then decide retention: the simplest way to remove personal data is to archive
  the backup securely and remove the database volume (`docker compose -f compose.prod.yaml down -v`).
