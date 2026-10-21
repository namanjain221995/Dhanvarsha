# Verification report

What was checked, how, and what the results were. Everything ran on the development host
described below with synthetic data only (`@example.com` and `loadtest.example.com`
addresses). These results are evidence for this environment. They are not evidence for a
real venue network, real phones, or an external email provider, except where stated.

## Environment

| Item | Value |
|---|---|
| Host | NVIDIA DGX-class workstation: aarch64 (Arm Cortex-X925), 20 CPUs, 121 GiB RAM. It was shared with other running services during the tests. |
| OS and runtime | Linux 7.0, Docker 29.6.2, Compose v5.2.0 |
| Images | postgres:17.11-alpine, caddy:2.11.7-alpine, axllent/mailpit:v1.31.6, python:3.12-slim (API and worker), node:22-bookworm-slim (web), grafana/k6:1.8.2, mcr.microsoft.com/playwright:v1.64.1-noble |
| Topology | One Docker host. Caddy proxies to Next.js and to FastAPI (2 uvicorn processes), which use PostgreSQL. One worker. k6 and Playwright run in containers on the same host. |
| PostgreSQL | `max_connections=200`, `shared_buffers=256MB` (development compose) |
| Isolation | Browser tests use Compose project `stagevote-e2e` (port 3180). Load tests use `stagevote-load` (port 3190). Each has its own database volume. Backend tests create a throwaway database per run. |

## Static checks

| Command | Result |
|---|---|
| `cd apps/api && uv run ruff check src tests` | All checks passed |
| `cd apps/api && uv run ruff format --check src tests` | All files formatted |
| `cd apps/api && uv run mypy` | Success: no issues found in 43 source files |
| `cd apps/web && npx tsc --noEmit` | No errors |
| `cd apps/web && npx eslint .` | No problems |
| `docker compose build` (api, web) | Both images build. `next build` compiles with output `standalone`. |
| `docker compose -f compose.prod.yaml -f compose.gmail.yaml config` | Valid. It was checked with placeholder values in a scratch directory. |
| Caddy `caddy validate` on `Caddyfile` and `Caddyfile.prod` | Valid configuration |
| Scan for leaked credential material or identifiers (`grep` for the pasted key's identifiers and `BEGIN PRIVATE KEY`) | None found |

## Backend integration tests (real PostgreSQL)

`cd apps/api && uv run pytest -q` reported **110 passed in 54.7 s** in the final run
(2026-10-20, after the invitation editor, one-step invitations, frameless logo and team members were added). Each run creates `stagevote_test_<hex>` owned by the owner role, migrates it with Alembic,
serves the app as the restricted runtime role, and drops the database at the end.

| File | Tests | What it proves |
|---|---|---|
| `test_import.py` | 5 | Paste and CSV formats, invalid rows, in-input duplicates, already-imported addresses. Matching is case-insensitive and keeps dots and `+tags`. Two concurrent commits of the same list (in different orders) neither duplicate nor deadlock. |
| `test_invitations.py` | 9 | The invitation sets the account password: Argon2id hash on the account, Fernet-encrypted copy in the job, plaintext nowhere. The email address (any case) and the emailed password sign in directly. Unknown, not-invited and wrong-password attempts answer identically. Re-sending replaces the password and supersedes the old email. Disabled accounts cannot be invited. The login window still applies. The retired activation and recovery endpoints do not exist. One-step *send invitations to* adds new addresses as participants (with names from `Name <email>`), emails everyone a real password that signs in, gives anyone invited before a new password, skips disabled accounts, and sends nothing if one address is invalid or more than 50 are listed. |
| `test_login_window.py` | 4 | `LOGIN_NOT_OPEN` and `LOGIN_CLOSED` hold whatever the client claims. The window opens exactly at database time. Deadlines are enforced before the sweep runs, and access closing stops voting. |
| `test_votes.py` | 6 | Twelve simultaneous identical submissions store exactly one row. Covers replay, `IDEMPOTENCY_KEY_REUSED`, `ALREADY_VOTED`, recovery after a lost response, and receipts after close, after logout and after a new login. |
| `test_manipulation.py` | 9 | Foreign nominees and ballots are refused, and the composite FK also refuses direct inserts. Voting is refused on upcoming rounds. Participants get no admin access and no access to other participants. Covers `BAD_ORIGIN`, `CSRF_FAILED`, and results visible only while published. |
| `test_cutoff_races.py` | 4 | Votes race close and start-next over eight random rounds each: every vote is either stored with `created_at <= closed_at` or refused, and both outcomes occur. A vote waiting on the live lock past the deadline is refused, with an accepted control case. Concurrent admin actions with the same `live_version` give exactly one `STALE_STATE`. |
| `test_sessions.py` | 5 | Four simultaneous logins leave one session. Covers cookie reuse, logout and expiry. An organiser reset, disabling, and a re-sent invitation each end the old session over HTTP and inside the vote transaction, then allow a new sign-in. |
| `test_schedule_worker.py` | 10 | Jobs per revision, opening with the planned end as deadline, and stale jobs after edits. Manual actions suspend the schedule and resume regenerates it. Stale or duplicate jobs never reopen an event, and two workers open a due event once. A missed window after a worker restart is recorded and does not open. Covers the deadline sweep, and overlap rejection by both the API and the database. |
| `test_rollback.py` | 2 | A failure after the vote INSERT gives a 500, with no vote, no receipt and no outbox notification. A failed transition leaves the live state untouched. |
| `test_email_delivery.py` | 13 | Transient errors back off, permanent errors fail once, and the attempt limit holds. A configuration error pauses the loop. `unknown` is retried only after the admin confirms. Lease expiry after send gives `unknown`; before send, the job is requeued. Two workers never send the same job. The emailed password is purged after acceptance and never appears in logs or responses. Superseded invitations are never sent. The status wording says accepted, not delivered. One real SMTP message reaches Mailpit with the email-address user ID, the password, the link and a plain-text part. |
| `test_email_templates.py` | 9 | Built-in wording until an organiser saves their own. Saved wording reaches invitations with placeholders filled, organiser text escaped (never HTML), and the user ID, password and link always present; resetting restores the built-in wording. Unknown placeholders, line breaks or control characters in single-line fields, empty or over-long fields and extra fields are refused and nothing is stored. The draft preview renders without storing or sending. A preview to several addresses sends one message each, marked `[Preview]`, with the example password. Bad or more than 10 addresses queue nothing. The programme logo is embedded as an inline PNG (`multipart/related`, `cid:`), and a missing logo file sends the email without an image. A programme logo upload loses its transparent margins (other image kinds keep their canvas), and the email shows it 56 px high at its own shape without a frame. |
| `test_team_members.py` | 2 | Team member names are cleaned (spaces, blanks, repeats), saved, and shown in the admin detail, the admin preview and the participant event. They can be corrected after voting opens, with an audit entry. A vote for the team gives a receipt naming the team. More than 30 names, line breaks or tabs, names over 120 characters and a plain string instead of a list are refused, and nothing is stored. |
| `test_restart.py` | 2 | *Restart from round 1* closes the live round, archives every round, and creates identical upcoming copies in order, with ballots, nominees and team members. Stored votes are unchanged. The participant's receipts disappear, and they can vote again on the copy, while the archived ballot refuses votes. It is audited. It is refused when nothing has started yet (`ALREADY_AT_START`) or with a stale live version. |
| `test_multiprocess_live.py` | 1 (scenario) | Two real uvicorn processes: an admin change through process B reaches a socket on process A within 2 s, and a session reset through B closes A's socket with code 4401. Killing the listener connections leads to a `resync`. A row inserted with the notify trigger bypassed arrives through the outbox catch-up poll within about 5 s. A bad Origin or forged cookie is rejected at the handshake. |
| `test_db_invariants.py` | 5 | The runtime role cannot UPDATE, DELETE or TRUNCATE votes or the audit log. The trigger blocks the owner too. There is at most one open event and one live session. Composite FKs reject inconsistent vote rows. |
| `test_admin_api.py` | 7 | Round numbers ignore archived events; nominee order freezes when voting opens; participant counts and completion ignore archived rounds; email jobs are scoped to the programme; organiser session status and logout signal; the hub closes only the signed-out admin session; names reject control characters (email-header safety). |
| `test_hardening.py` | 6 | Twenty parallel wrong passwords produce at most 8 `401`s (the rest `429`), and the right password is refused while locked; a lockout looks identical for unknown and existing accounts; a successful sign-in clears the counter; a frozen nominee cannot be renamed into a sibling's name; a revocation closes only the named sessions; images over 40 megapixels are refused before decoding. |

Bugs found by tests and review, all fixed and covered:

1. Two overlapping imports in different orders deadlocked; rows are now inserted in sorted order.
2. Unarchiving into a schedule clash returned 500 at COMMIT; it now gives 422.
3. Changing the run mode returned a stale `live_version`, which made the next live action fail.
4. A restore with `--no-privileges` left the runtime role without grants; `stagevote bootstrap` now re-applies them and `check-privileges` verifies required access too.
5. After an offline period the participant page could stay "Waiting for connection" for up to 60 s; it now refreshes on reconnect and on `online`, and never keeps two sockets.
6. Signing out of the admin console redirected to the wrong page because the logout signal also closed the console's own socket; fixed.
7. Independent review (2 medium, 7 low; none critical or high), all fixed:
   - Unsubmitted drafts and pending attempts in sessionStorage were not tied to the participant, so a shared browser could resend one person's pending choice for the next person. They are now keyed by participant and cleared on sign-in, sign-out and session end.
   - The Gmail provider shared one httplib2 connection across concurrent sends; each send now uses its own connection, with credential refreshes serialised.
   - Ballot and nominee editors could deadlock with live transitions; they now take the live lock first.
   - Login rate limiting is now an atomic per-identifier counter with an existence-independent key; the per-IP failure cap is 1,000 per 10 minutes so one attendee cannot easily lock out a venue IP.
   - Post-freeze renames can no longer move a nominee towards a sibling's name.
   - Session revocations name the revoked session ids, so a newer valid session keeps its socket.
   - Submit also requires the round detail to be as current as the snapshot.
   - Images are rejected above 40 megapixels before decoding; SQL statement parameters are never logged.

## Browser checks (Playwright)

Run with `tests/e2e/run.sh`. Playwright 1.64.1 runs in its official container against the
isolated stack. The real worker delivers invitations into the stack's Mailpit, and the
tests read them through the Mailpit API.

Final run (`tests/e2e/run.sh`, all projects): **32 passed, 0 failed** (34 skipped by design:
each spec targets the projects listed below). After the invitation editor, one-step invitations and team members were added
(2026-10-20), the full run again gave **32 passed, 0 failed** (34 skipped by design).

| Project | Engine | Specs run | Result |
|---|---|---|---|
| `mobile-chromium` | Chromium, Pixel 7 profile | journey, access, resilience, layout and accessibility, admin drawer | 15 passed |
| `mobile-webkit` | WebKit, iPhone 14 profile (emulation, **not** real-device Safari) | journey, resilience | 7 passed |
| `desktop-chromium` | Chromium, 1440×900 | organiser console (7), accessibility (3) | 10 passed |

Organiser console specs (desktop): sign-in, unauthenticated redirect and sign-out; programme settings with logo upload and concurrent-edit handling; import preview counts (new, invalid, duplicate, already imported), import and invitation email in Mailpit; event, ballot and nominee editing; live control open → stale action refused → start next → close voting through confirmation dialogs; results with tie label, CSV export header without per-person data, publishing visible to a participant; email diagnostics; editing the invitation wording with the live preview (organiser text shown escaped), saving it, sending real invitations to two new addresses at once through the confirmation dialog (both are added as participants and each reaches Mailpit with its own address as user ID, a real password and the edited wording) and restoring the built-in wording. Mobile: the navigation drawer keeps focus, closes with Escape and navigates without overflow.

What the participant specs exercise:

- **Journey:** the invitation email arrives in Mailpit with the user ID (the participant's email address) and the password in both parts, and no password in the link. The sign-in page offers no sign-up, set-up or forgot-password links. The participant signs in with those details and sees the waiting lobby. The live card appears without a reload when the organiser opens a round. A choice is selected and submitted, and the receipt appears only after confirmation. The locked receipt survives a reload. The next round opens while the detail page is visible ("Next event is live", navigate, vote). The programme ends with the thank-you screen listing receipts. The aggregate equals the votes cast.
- **Access:** the countdown before sign-in opens. With the browser clock set one day ahead (`page.clock`), the page still follows server time, and a direct API login is refused with `LOGIN_NOT_OPEN`. Sign-in enables itself when the countdown ends. The same account can be signed in on two devices at once; a reload keeps the session, and an organiser's *Sign out everywhere* signs both open pages out live. A lost password is handled by the organiser re-sending the invitation: the open session is signed out live, the old password stops working and the new emailed password signs in.
- **Resilience:**
  - A committed vote whose response is lost shows "Checking your vote…" with no other choice possible, then the receipt. It is stored once with one idempotency key.
  - A request that never reached the server is retried with the same key.
  - Offline: the banner shows, submission pauses, the draft is kept, and voting resumes after reconnecting.
  - A round closing while its ballot is on screen: nothing is submitted.
  - The next round opening while the previous ballot is visible: the unsubmitted choice is not transferred and the new ballot starts empty.
  - The lobby reveals a new live round without a reload, and a two-ballot event gives separate receipts.
- **Layout:** at 320, 375, 390, 430, 768 and 1440 px, the sign-in, lobby and ballot pages have no horizontal overflow. The last nominee stays above the sticky submit bar, which stays in the viewport. A deliberately long round name wraps.
- **Accessibility:** axe-core (WCAG 2.0/2.1 A and AA rules) finds no serious or critical violations on the sign-in, lobby and ballot pages. Voting works with the keyboard only (Tab, then arrow keys in the radio group, then Enter) with a visible focus ring. With reduced motion, the confirmation is static (the confetti canvas paints nothing) and the receipt shows the same text.

## Load tests (k6)

Run with `tests/load/run.sh --yes-synthetic-load`. Each run seeds a fresh synthetic programme in
the isolated `stagevote-load` project, then runs k6 inside the project network. CPU and RAM are
sampled every 5 s, and the stored votes are reconciled against the receipts acknowledged to
clients.

### Main scenario: 500 participants, 3 rounds, 10-second vote bursts (final run `20261019T211934Z`)

- 500 virtual participants sign in over 30 s and keep a WebSocket open like a phone. Each round, every one of them votes within 10 s of the round opening.
- 10% send the same request twice at once.
- 5% simulate a lost response (2–15 ms client timeout), recover through the receipt or a same-key retry, then try to change their vote with a new key.
- The organiser runs open → start-next → start-next → close with 40 s rounds while this traffic flows.

| Measure | Result |
|---|---|
| Duration | 216 s, 6,818 HTTP requests, 500 WebSocket sessions, 2,000 state signals delivered |
| Votes acknowledged to clients / stored in PostgreSQL | **1,500 / 1,500 (match)** |
| Duplicate (participant, ballot) rows, cross-ballot references | **0 / 0** |
| Unexpected errors / acknowledged receipts missing or changed | **0 / 0** |
| Duplicate-retry pairs | 150 (each gave one 201 and one 200 replay) |
| Lost responses simulated | 75. Of these, 29 were recovered by reading the receipt and 27 by a same-key retry; the rest got their response in time. All 75 later "change my vote" attempts got `409 ALREADY_VOTED`. |
| Vote submission latency | p50 12.6 ms, **p95 20.6 ms**, p99 28.4 ms, max 106.2 ms (target: p95 < 1 s) |
| State propagation (commit to client message) | p50 12.6 ms, **p95 24.0 ms**, p99 27.2 ms, max 27.2 ms (target: p95 < 2 s) |
| Snapshot refetch after a transition (500 clients, 0–700 ms jitter) | p50 6.8 ms, p95 56 ms, p99 117 ms |
| Login (Argon2id) | p50 43 ms, p95 55 ms, p99 72 ms |
| Admin transition | p50 50 ms, max 53 ms |
| `http_req_failed` rate | 2.32%. All of these are deliberate: the client-side timeouts of the lost-response simulation, plus the expected 409s for change attempts. |
| Peak CPU / RAM | api (2 processes) 96% / 334 MiB, db 40% / 135 MiB, proxy 9% / 169 MiB, web 0% / 43 MiB, worker 1% / 64 MiB |

Earlier runs of the same scenario reconciled exactly as well (`20261019T201309Z`: 1,500 / 1,500;
`20261019T195740Z`: 1,497 / 1,497, whose single "unexpected error" was a test-harness bug —
two virtual users shared an account and the server correctly answered
`SESSION_ACTIVE_ELSEWHERE`).

### Pages and phone animation (2026-10-21, run `20261021T150114Z`)

The main scenario now also loads pages the way a phone does. Each virtual participant opens the
sign-in page (the HTML, then every script, stylesheet and font it names), the lobby after
signing in, and each round's page when the round opens. Files it already has are not fetched
again, as in a browser. Same settings as above, with 8 API processes as on the production server.

| Measure | Result |
|---|---|
| Duration | 216 s, 18,300 HTTP requests, 2,500 page opens, 8,000 static files, 500 WebSocket sessions |
| Votes acknowledged / stored | **1,500 / 1,500 (match)**, 0 unexpected errors |
| Full page open (HTML and every new file) | p50 12 ms, **p95 33 ms**, p99 45 ms, max 132 ms (target: p95 < 2 s) |
| Page HTML | sign-in p95 19 ms, lobby p95 23 ms, round p95 21 ms |
| Static files | p95 17 ms, 0 failed |
| Peak CPU / RAM | web 91% of one core / 115 MiB, api 223% / 831 MiB, db 71% / 170 MiB, proxy 41% / 105 MiB |

In production, Cloudflare caches the scripts, styles and fonts (`cache-control: public,
max-age=31536000, immutable`; `cf-cache-status: HIT` on repeat requests). The server's web process
therefore serves mostly the small HTML pages, a quarter of the requests measured here.

**Phone animation cost**, measured with Chrome traces on a 390 px phone at 3x pixel density,
with the CPU slowed 4x, 6x and 10x to stand in for mid-range and low-end phones:

- The "Live now" lamp animated `box-shadow`, which repainted the card it sits on on every frame:
  about 180 repaints a second and 150 ms of paint per second on the lobby. It now pulses a ring
  with `transform` and `opacity` only. Repaints fell to 2 a second, and main-thread work on the
  lobby fell about 4.5x (paint 147 to 1 ms per second, raster 19 to 0.3 ms per second).
- The coin rain and the stage sign-in cause no repaints. They cost style work only.
- Frame rate with the CPU slowed 6x: sign-in, lobby and round pages hold 60 fps with no slow
  frames. Slowed 10x: sign-in 60 fps, lobby 56 fps, round 54 fps (one frame over 34 ms in 6 s).
- Tapping a team on the round page shows the choice on the next frame within 23–34 ms at full
  speed, 28–60 ms slowed 6x, and 59–72 ms slowed 10x.

### Cutoff-race scenario: votes straddling transitions (`20261019T202527Z`)

`tests/load/run.sh --yes-synthetic-load --voters 300 --rounds 3 --round-seconds 15 --burst 25 --login-ramp 20`.
Voters take up to 25 s to vote, but each round lasts only 15 s before *start-next*. Many
submissions therefore arrive just before, during or after a transition.

| Measure | Result |
|---|---|
| Votes acknowledged / stored | **557 / 557 (match)**. Per round: 192, 184 and 181. |
| Submissions refused with `VOTING_CLOSED` | 380. These arrived after their round closed. None of them was stored, and none was acknowledged. |
| Duplicates, cross-ballot references, unexpected errors, receipt mismatches | **0, 0, 0, 0** |
| Vote latency | p50 12.2 ms, p95 22.1 ms, p99 26.4 ms |
| State propagation | p50 9.6 ms, p95 17.5 ms, p99 18.5 ms |
| `http_req_failed` rate | 11.45%. These are the expected 409 refusals and the simulated timeouts. |

Together with `test_cutoff_races.py`, this shows that a transition never loses an
acknowledged vote and never accepts one after the cutoff, under concurrent traffic.

### API stress mode (2026-10-20)

`tests/load/run.sh --yes-synthetic-load --users 500 --requests N` makes 500 signed-in
synthetic users share N API calls. Each user sends its next call as soon as the previous
answer arrives (35% snapshot, 25% live event, 10% time, 10% public programme, 20% vote then
receipt). Every run writes an HTML report: `tests/load/results/<run>/report.html`. Runs were
on the 20-core development host with 6 API workers.

| Requests | Pause | Failed | Average / sustained speed | p50 | p95 | p99 | Votes accepted = stored |
|---|---|---|---|---|---|---|---|
| 10,000 | none | 0 | 973 / 1,322 per s | 351 ms | 1.56 s (over the 1 s target) | 2.74 s | 442 = 442 |
| 10,000 | 1 s | 0 | 459 / 499 per s | 8.8 ms | 663 ms | 2.43 s | 460 = 460 |
| 100,000 | none | 0 | 2,101 / 2,316 per s | 184 ms | 551 ms | 920 ms | 500 = 500 |
| 1,000,000 | none | 0 | 1,936 / 2,555 per s | 203 ms | 617 ms | 963 ms | 500 = 500 |

The million-request run lasted 8 min 37 s with no failures. Throughput and waiting time were
steady from the first minute to the last. API memory stayed at about 3.0 GB from start to end,
so no leak was seen. The slowest step is 500 sign-ins in the same second: about 2 s typical
and up to 3.4 s, because password hashing (Argon2id) is deliberately expensive. In the short
runs this login burst dominates p95 and p99. These numbers are for this host; measure the
production server separately.

## Other checks

| Check | How | Result |
|---|---|---|
| Invitation throughput (local Mailpit only) | 310 invitations queued through `/admin/invitations/send` on the e2e stack | Queued in 1.6 s, including Argon2 hashing. All 310 were accepted by Mailpit after 140 s, which matches the default pacing of `EMAIL_MAX_PER_MINUTE=120`. 300 messages for the batch were in the inbox. |
| Backup and restore drill | `scripts/backup.sh -p stagevote-e2e`, then `scripts/restore.sh --yes-replace-database … -- -p stagevote-e2e` | Data matched before and after (21 votes, 342 participants), `stagevote reconcile` passed, `check-privileges` passed, and readiness returned ok. The first drill found a real bug: a restore with `--no-privileges` left the runtime role without grants, so readiness failed. `stagevote bootstrap` now re-applies the runtime grants every time, and `check-privileges` now also verifies the required reads and writes. The drill passed after the fix. |
| CSP | Load pages in the production build | Every script carries the per-request nonce. Pages hydrate with no CSP violations. |
| Gmail Workspace provider (key supplied by the owner for testing) | Admin › Email › Run diagnostics on the main stack | Key file readable by the app user only, sender `hello@techsarasolutions.com` configured, **token exchange refused by Google: `unauthorized_client`** (domain-wide delegation for this client ID and the `gmail.send` scope is not authorised). No email was sent. Provider not verified. |
| Production configuration refusal | `Settings.validate_for_runtime` | Refuses Mailpit, plain-text SMTP, missing SMTP credentials, non-HTTPS base URLs and example.com senders when `APP_ENV=production`. |

## Not executed or not verifiable here

- **Real-device Safari, Android Chrome on real phones, and the venue's Wi-Fi.** The WebKit and Chromium runs are emulations on one host.
- **External email providers.** SMTP was not configured. The Gmail API provider is configured with the owner's test key, but Google refused the delegated token (`unauthorized_client`), so no message has been accepted by a real provider. Inbox placement, SPF, DKIM and DMARC are not verified.
- **Production HTTPS deployment.** `compose.prod.yaml` and `Caddyfile.prod` are validated as configuration only. No certificate was issued, because no domain was used.
- **Load across multiple hosts or through the internet.** The load generator and the stack share one host, so network latency is near zero. Venue Wi-Fi will add latency and loss.
