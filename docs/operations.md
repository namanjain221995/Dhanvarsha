# Operator runbook

For the people who run StageVote before and during a live programme. Commands assume the
repository root. Production uses `compose.prod.yaml` (add `-f compose.gmail.yaml` for the
Gmail API provider); development uses `compose.yaml`.

## Development and production differ on purpose

| | Development (`compose.yaml`) | Production (`compose.prod.yaml`) |
|---|---|---|
| URL | `http://localhost:<STAGEVOTE_HTTP_PORT>` (bootstrap picks a free port, 3000 first) | `https://<SITE_DOMAIN>` with automatic certificates (ports 80/443) |
| Cookies (session, device, organiser) | plain names (`sv_session`, `sv_device`, `sv_admin`), not `Secure` (HTTP on localhost) | `__Host-` names, `Secure`, `HttpOnly`, `SameSite=Lax` |
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
  invitation contains the participant's personal sign-in link behind its button. The link
  is valid as soon as the email arrives; opened before sign-in opens, the page shows the
  countdown and nothing is used up. Watch the email page until every invitation is
  `accepted`; follow up on `failed` and `unknown`. Ask participants to keep the email and
  to open it on the phone they will vote with: the first phone or browser that taps the
  button keeps the link.
- **Backups**: `scripts/backup.sh -f compose.prod.yaml` and practise a restore on a
  spare machine (see below).

## Loading the Dhanvarsha audience polls

The audience polls of the event brief ship with the API
(`apps/api/src/stagevote/data/dhanvarsha_audience_polls.json`, wording exactly as in the
brief). `stagevote load-polls` puts them into the active programme in one step: one round
per poll with one ballot (the question) and its options in order. P01–P14 become rounds
1–14 and the optional break polls B01–B02 come last, so the main sequence keeps its
numbers; open B01/B02 ("Break question 1" and "2") by hand during the break after P09.
Round names carry no poll id: voters see "TechSara Placement League", not "P04 — …".
Rounds loaded by the first release, named "P04 — …", are renamed in place, votes and all,
the next time the command runs. Each round carries an
**organiser note** (when to open it and any special instruction, such as P04's "the
official winner is decided by the highest bid"), shown only in the console.

```sh
# Local (development stack; rebuild the image first if the code changed)
docker compose run --rm migrate stagevote load-polls --archive-others
# Production, after deploying the release that adds organiser notes (migration 0010)
docker compose -f compose.prod.yaml run --rm migrate stagevote load-polls --archive-others
```

- It refuses to run while any round is open (close it in *Live control* first) and then
  changes nothing.
- Rounds are matched by their exact name. A missing poll is created; a round that never
  opened is brought in line with the file; a round that already opened keeps its ballot
  and options (they froze when voting opened) and only gets the note.
- The polls take the first places in the running order; other rounds follow.
  `--archive-others` archives every other round, exactly like *Archive* in the console:
  votes are kept, and an archived round can be restored from *Events* (the *Archived*
  panel › *Restore…*). Leave the flag out to keep the other rounds after the polls.
- It prints one line per poll (`created`, `updated`, `unchanged` or
  `kept (already opened)`) and one per archived round. Running it again changes nothing.
- `--file other.json` loads another file of the same shape (inside the container, so mount
  or copy it in first). Notes stay editable afterwards in the event editor, also after the
  round has opened; every change is in the audit log.

## The day before

- Rehearse with a test round and two or three test participants on real phones over the
  venue Wi-Fi and mobile data. Open at least one invitation from inside an email app (Gmail,
  Outlook) and tap *Enter the arena* there, then open the same link in the phone's normal
  browser (opening alone binds nothing: only the tap does). An app that opens links in a
  built-in browser of its own keeps that browser's cookies apart, so the link then belongs
  to the app and the normal browser says it is in use; knowing which apps do this on your
  phones helps the help desk. Archive the test round afterwards (votes stay in the
  database but the round disappears from the programme).
- After a full rehearsal of the real rounds, use *Live control › Start again › Restart from
  round 1*. It closes any open round, archives every round with its votes (votes are never
  deleted; they stop counting and are hidden from participants and results) and creates
  identical fresh copies, all upcoming, in the same order. Participants stay signed in and
  can vote again. Planned times already in the past are cleared.
- Check: *Live control* shows the worker as healthy and the live channel connected;
  `curl -fsS https://<domain>/api/v1/ready` returns `"status":"ok"`.
- Print the help-desk sheet (below). Most sign-in problems are solved by resending the
  invitation.

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

Each invitation link works on one device: the first phone or browser that taps its button.
Most answers below come down to a new invitation: Participants › find them › *Resend
invitation* (the button reads *Send invitation* for someone never invited). It emails a new
link, the old link stops working at once, every device they were signed in on is signed
out, and the new link opens on the first device that taps it. There is no self-service
recovery by design.

| Participant says | Do this |
|---|---|
| "It says the link is already in use on another phone or browser" | The link was first opened somewhere else: another phone or computer, another browser, or an email app's built-in browser (some email apps open links in a browser of their own, with cookies separate from Safari and Chrome). If that was them in another app, they open the link there. Otherwise *Resend invitation*, and they tap the new email's button on the phone in hand. |
| "I have a new phone" or "I want to vote on another device" | *Resend invitation*, then they open the new email on the new device. The old device is signed out and the old link stops working. |
| "I signed out by mistake" or "it says my sign-in has ended" | Nothing to do in the console: they open the invitation email again **on the same device** and tap the button. This works after signing out and after the 12-hour session ends. If that device refuses the link, *Resend invitation*. |
| "I lost my phone" or "someone else is using my account" | *Resend invitation*. The old link stops working and every device is signed out; they tap the new email's button straight away on the device in hand (if the lost phone can still read their email, it could tap first). *Sign out everywhere* alone is not enough here: it keeps the link on the old phone, which can sign straight back in with it. |
| "The email never arrived" or "I deleted it" | Check the address on the participant row and the email page status; fix the address if needed and *Send invitation* (or *Resend invitation*). Ask them to look in spam. Only the newest invitation works. |
| "It says the sign-in link is not valid any more" | They opened an older invitation. The newest email works; if they cannot find it, *Resend invitation*. |
| "The site asks me to open my invitation email" | They opened the site without the link. They open the invitation email on this device and tap the button. |
| "Did my vote count?" | Their receipt is on the round page and in the lobby. Admins can see that a ballot was voted (completion), never the choice. |
| "The page says Reconnecting" | Usually venue Wi-Fi. Voting pauses until the phone confirms the latest state; the page recovers on its own. Switching to mobile data is fine; the session continues. |

## Email problems

- Run *Admin › Email › Run diagnostics*. Typical causes: wrong SMTP password
  (`smtp_auth_failed`), delegation not authorised or not yet propagated
  (`unauthorized_client`), wrong sender mailbox (`invalid_grant`).
- After a configuration fix, *Retry all failed* requeues failed invitations that still have
  their encrypted copy of the sign-in link. Older ones need a fresh invitation (a new link).
- `unknown` means the provider may already have the message. Retry only if a duplicate is
  acceptable (the same link is resent).
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
- Dumps contain participant emails, the hashes of sign-in links and devices, organiser
  password hashes, and the retired password hashes of voters invited before links: keep
  them encrypted and on restricted storage. After a restore, the valid sign-in links are
  the ones that were valid when the dump was taken: invitations sent later stop working
  and need sending again.
- `restore.sh` is destructive: it stops the API and worker, drops and recreates the
  database, restores, reapplies migrations and grants, restarts, and checks privileges.
- `stagevote reconcile` compares stored votes with tallies and checks for duplicates and
  cross-ballot references.

## Uploaded media and disk space

- Every uploaded file lives in the `media` volume (`/data/media` in the API containers,
  served by Caddy from `/srv/media`): pictures as WebP, and the images and videos opening-page
  cards show full screen. Videos are kept exactly as uploaded. There is no size limit by
  default (`MAX_MEDIA_BYTES` and `MAX_MEDIA_IMAGE_BYTES` are 0; set them in `.env` to add
  one). Images shown full screen may have up to 250 megapixels and are stored as WebP of at
  most 3840 px.
- An upload is refused (507) unless the volume keeps 1 GiB free beyond the file. Unfinished
  uploads wait in `/data/media/.uploads/` and are removed the next time an upload starts
  after 24 hours without a chunk. Check the space with
  `docker compose -f compose.prod.yaml exec api df -h /data/media`.
- Files are never deleted by the application: removing a card's video keeps the file. Videos
  are part of the media archive that `scripts/backup.sh` writes, so backups grow with them
  (allow the sum of the videos). Unfinished uploads in `.uploads/` are left out of it.
- Behind Cloudflare, each chunk is one 8 MiB request, well under its 100 MB limit.

## Secrets and rotation

| Secret | Rotation effect |
|---|---|
| `APP_SECRET_KEY` | CSRF tokens change; open pages need a reload. Sessions, sign-in links and device bindings stay valid. |
| `INVITATION_ENCRYPTION_KEY` | Queued invitation emails can no longer be decrypted (they fail with `payload_unreadable`); send those invitations again. Links already delivered keep working. |
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
