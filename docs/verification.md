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
| `cd apps/api && uv run mypy` | Success: no issues found in 44 source files |
| `cd apps/web && npx tsc --noEmit` | No errors |
| `cd apps/web && npx eslint .` | No problems |
| `docker compose build` (api, web) | Both images build. `next build` compiles with output `standalone`. |
| `docker compose -f compose.prod.yaml -f compose.gmail.yaml config` | Valid. It was checked with placeholder values in a scratch directory. |
| Caddy `caddy validate` on `Caddyfile` and `Caddyfile.prod` | Valid configuration |
| Scan for leaked credential material or identifiers (`grep` for the pasted key's identifiers and `BEGIN PRIVATE KEY`) | None found |

## Backend integration tests (real PostgreSQL)

`cd apps/api && uv run pytest -q` reported **110 passed in 54.7 s** in the final run
(2026-10-20, after the invitation editor, one-step invitations, frameless logo and team members were added). That run still signed voters in with passwords. On 2026-10-28
personal sign-in links replaced voter passwords, and the sign-in tests were rewritten for
them (see *Sign-in links* below). The final run after that change reported **122 passed in
41.9 s**. Each run creates `stagevote_test_<hex>` owned by the owner role, migrates it with Alembic,
serves the app as the restricted runtime role, and drops the database at the end.

| File | Tests | What it proves |
|---|---|---|
| `test_import.py` | 5 | Paste and CSV formats, invalid rows, in-input duplicates, already-imported addresses. Matching is case-insensitive and keeps dots and `+tags`. Two concurrent commits of the same list (in different orders) neither duplicate nor deadlock. |
| `test_invitations.py` | 9 | The invitation sets a personal sign-in link: only the token's SHA-256 is stored on the participant, the token is Fernet-encrypted in the job under `link_token`, and the plaintext is nowhere else. The emailed link signs in. Re-sending replaces the link (the old one gets `LINK_INVALID`) and supersedes the old email. Disabled accounts cannot be invited. The login window still applies. The retired activation and recovery endpoints, and the former voter sign-in route `POST /api/v1/auth/login`, do not exist. One-step *send invitations to* adds new addresses as participants (with names from `Name <email>`), emails everyone a working link, gives anyone invited before a new link, skips disabled accounts, and sends nothing if one address is invalid or more than 50 are listed. |
| `test_login_window.py` | 4 | `LOGIN_NOT_OPEN` and `LOGIN_CLOSED` hold whatever the client claims, and a refused tap leaves the link unbound. The window opens exactly at database time. Deadlines are enforced before the sweep runs, and access closing stops voting. |
| `test_votes.py` | 6 | Twelve simultaneous identical submissions store exactly one row. Covers replay, `IDEMPOTENCY_KEY_REUSED`, `ALREADY_VOTED`, recovery after a lost response, and receipts after close, after signing out and after entering again with the link. |
| `test_manipulation.py` | 11 | Foreign nominees and ballots are refused, and the composite FK also refuses direct inserts. Voting is refused on upcoming rounds. Participants get no admin access and no access to other participants. Covers `BAD_ORIGIN`, `CSRF_FAILED`, and results visible only while published. A cross-site tap on a sign-in link is refused and binds nothing. The device cookie on its own signs nobody in, also when it is sent as a session or organiser cookie. |
| `test_cutoff_races.py` | 4 | Votes race close and start-next over eight random rounds each: every vote is either stored with `created_at <= closed_at` or refused, and both outcomes occur. A vote waiting on the live lock past the deadline is refused, with an accepted control case. Concurrent admin actions with the same `live_version` give exactly one `STALE_STATE`. |
| `test_sessions.py` | 7 | Sign-in with the link and the one-device rule (see *Sign-in links* below): session reuse in the same browser, logout and expiry. An organiser reset, disabling, and a re-sent invitation each end the open session over HTTP and inside the vote transaction. |
| `test_schedule_worker.py` | 10 | Jobs per revision, opening with the planned end as deadline, and stale jobs after edits. Manual actions suspend the schedule and resume regenerates it. Stale or duplicate jobs never reopen an event, and two workers open a due event once. A missed window after a worker restart is recorded and does not open. Covers the deadline sweep, and overlap rejection by both the API and the database. |
| `test_rollback.py` | 2 | A failure after the vote INSERT gives a 500, with no vote, no receipt and no outbox notification. A failed transition leaves the live state untouched. |
| `test_email_delivery.py` | 15 | Transient errors back off, permanent errors fail once, and the attempt limit holds. A configuration error pauses the loop. `unknown` is retried only after the admin confirms. Lease expiry after send gives `unknown`; before send, the job is requeued. Two workers never send the same job. The emailed link token is purged after acceptance and never appears in logs or responses. Superseded invitations are never sent, and a job queued before links, with a password in its payload, fails as `retired_payload`. A template that cannot be filled in fails the job as `template_error` (keeping the link copy for a retry) instead of retrying it forever. The status wording says accepted, not delivered. One real SMTP message reaches Mailpit with the personal link (`/enter#token=…`) and a plain-text part. |
| `test_email_templates.py` | 9 | Built-in wording until an organiser saves their own. Saved wording reaches invitations with placeholders filled, organiser text escaped (never HTML), and the button, the personal link and the fixed note always present; resetting restores the built-in wording. Unknown placeholders, line breaks or control characters in single-line fields, empty or over-long fields and extra fields are refused and nothing is stored. The draft preview renders without storing or sending. A preview to several addresses sends one message each, marked `[Preview]`, with only the example link `#token=example`. Bad or more than 10 addresses queue nothing. The programme logo is embedded as an inline PNG (`multipart/related`, `cid:`), and a missing logo file sends the email without an image. A programme logo upload loses its transparent margins (other image kinds keep their canvas), and the email shows it 56 px high at its own shape without a frame. |
| `test_team_members.py` | 2 | Team member names are cleaned (spaces, blanks, repeats), saved, and shown in the admin detail, the admin preview and the participant event. They can be corrected after voting opens, with an audit entry. A vote for the team gives a receipt naming the team. More than 30 names, line breaks or tabs, names over 120 characters and a plain string instead of a list are refused, and nothing is stored. |
| `test_restart.py` | 2 | *Restart from round 1* closes the live round, archives every round, and creates identical upcoming copies in order, with ballots, nominees and team members. Stored votes are unchanged. The participant's receipts disappear, and they can vote again on the copy, while the archived ballot refuses votes. It is audited. It is refused when nothing has started yet (`ALREADY_AT_START`) or with a stale live version. |
| `test_multiprocess_live.py` | 1 (scenario) | Two real uvicorn processes: an admin change through process B reaches a socket on process A within 2 s, and a session reset through B closes A's socket with code 4401. Killing the listener connections leads to a `resync`. A row inserted with the notify trigger bypassed arrives through the outbox catch-up poll within about 5 s. A bad Origin or forged cookie is rejected at the handshake. |
| `test_db_invariants.py` | 6 | The runtime role cannot UPDATE, DELETE or TRUNCATE votes or the audit log. The trigger blocks the owner too. There is at most one open event. A participant may hold several session rows, but session token hashes stay unique. Composite FKs reject inconsistent vote rows. Two participants can never hold the same sign-in link, and an activated participant must have a link (or a password from before links). |
| `test_admin_api.py` | 7 | Round numbers ignore archived events; nominee order freezes when voting opens; participant counts and completion ignore archived rounds; email jobs are scoped to the programme; organiser session status and logout signal; the hub closes only the signed-out admin session; names reject control characters (email-header safety). |
| `test_hardening.py` | 4 | Unknown sign-in links count against the client address and are refused past the per-IP limit, while a refusal on another device (`LINK_IN_USE`) is never counted (this replaces the per-identifier lockout tests of the old password sign-in); a frozen nominee cannot be renamed into a sibling's name; a revocation closes only the named sessions; images over 40 megapixels are refused before decoding. |
| `test_migrations.py` | 1 (scenario) | Migration 0006 on a throwaway database of its own: the upgrade adds the four nullable link columns, and a participant can then be activated with a link alone (but not with no credential at all). Existing rows and the retired password column are untouched, and the runtime role can use the new columns. The downgrade refuses while any participant holds a sign-in link and changes nothing; without links it removes the link columns and puts the previous activation check back, and upgrading again works. |
| `test_sign_in_tools.py` | 4 | `stagevote sign-in-link` replaces a participant's link exactly as an invitation does (the old link gets `LINK_INVALID`, the device is released, sessions end, the old email is superseded, the action is audited) but prints the link instead of emailing it. It is refused in production and for unknown or disabled people. `seed-loadtest` writes one working token per synthetic account, and `seed-demo --activate-sample` gives sample accounts sign-in links instead of a password. |

**Sign-in links** (added 2026-10-28, all passing in the run above). The backend tests for
link sign-in cover (mostly in `test_sessions.py`, `test_invitations.py`, `test_hardening.py`,
`test_email_delivery.py`, `test_email_templates.py`, `test_migrations.py` and
`test_sign_in_tools.py`):

- The check answers `ready`, `signed_in`, `in_use`, `invalid` and `disabled`, and sets a
  device cookie when the browser has none. It never binds, signs in or writes the audit log.
- The first tap binds that browser. The same browser enters again after signing out and
  after its session expired. A second browser gets `LINK_IN_USE`, which is audited and not
  counted against the per-IP failure limit.
- Two first taps from different browsers at the same moment, lined up at the participant
  lock: exactly one binds.
- A tap before the sign-in window opens is refused and binds nothing.
- The response to the first tap is lost: the retry from the same browser (same device
  cookie) signs in instead of getting `LINK_IN_USE`.
- A re-sent invitation rotates the token (the old link gets `LINK_INVALID`), clears the
  binding and signs open sessions out live.
- The email job holds the token under `link_token`. The rendered invitation contains
  `/enter#token=` and no user ID or password card. Preview and test emails contain only
  `#token=example`. A job with an old `{"password": …}` payload fails as `retired_payload`.
  The invitation, the preview job and the console preview each render with every template
  variable defined.
- Migration 0006 upgrades and downgrades, and its downgrade refuses while any sign-in link
  exists.
- `POST /api/v1/auth/login` no longer exists (`404` or `405`).

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
   - Login rate limiting is now an atomic per-identifier counter with an existence-independent key; the per-IP failure cap is 1,000 per 10 minutes so one attendee cannot easily lock out a venue IP. (This was for password sign-in; sign-in links have no identifier and keep only the per-IP cap.)
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
(2026-10-20), the full run again gave **32 passed, 0 failed** (34 skipped by design). Both
runs signed voters in with a password. The specs described below were rewritten for sign-in
links on 2026-10-28. With them the full run gave **34 passed, 0 failed** (38 skipped by
design) twice in a row, the second time after the last code change.

| Project | Engine | Specs run | Result (2026-10-20, password sign-in) | Result (2026-10-28, sign-in links) |
|---|---|---|---|---|
| `mobile-chromium` | Chromium, Pixel 7 profile | journey, access (5), resilience, layout and accessibility, admin drawer | 15 passed | 17 passed |
| `mobile-webkit` | WebKit, iPhone 14 profile (emulation, **not** real-device Safari) | journey, resilience | 7 passed | 7 passed |
| `desktop-chromium` | Chromium, 1440×900 | organiser console (7), accessibility (3) | 10 passed | 10 passed |

Organiser console specs (desktop): sign-in, unauthenticated redirect and sign-out; programme settings with logo upload and concurrent-edit handling; import preview counts (new, invalid, duplicate, already imported), import and invitation email in Mailpit (the dialog describes the personal sign-in link, and the email carries the link and no user ID or password); event, ballot and nominee editing; live control open → stale action refused → start next → close voting through confirmation dialogs; results with tie label, CSV export header without per-person data, publishing visible to a participant; email diagnostics; editing the invitation wording with the live preview (organiser text shown escaped), saving it, sending real invitations to two new addresses at once through the confirmation dialog (both are added as participants and each reaches Mailpit with its own personal sign-in link, never the preview example, and the edited wording) and restoring the built-in wording. Mobile: the navigation drawer keeps focus, closes with Escape and navigates without overflow.

What the participant specs exercise:

- **Journey:** the invitation email arrives in Mailpit with the personal link `<base>/enter#token=…` (no query string) in both parts, and no user ID or password. The page it opens has one enabled entry button and nothing to type, no sign-up, set-up or reset links, and the token has left the address bar. The participant taps the button and sees the waiting lobby. The live card appears without a reload when the organiser opens a round. A choice is selected and submitted, and the receipt appears only after confirmation. The locked receipt survives a reload. The next round opens while the detail page is visible ("Next event is live", navigate, vote). The programme ends with the thank-you screen listing receipts. The aggregate equals the votes cast.
- **Access:**
  - The countdown before sign-in opens, with the entry button disabled. With the browser clock set one day ahead (`page.clock`), the page still follows server time, and a direct API tap is refused with `LOGIN_NOT_OPEN` and leaves the link unbound. The button enables itself when the countdown ends, and the same link enters.
  - One device per link: a copy opened first in another browser, without a tap, binds nothing. The voter's phone taps and keeps the link; the copy's later tap, and any later opening of it, shows "already in use" with no button and no session. No request URL from either browser contains the token, which travels only in the bodies of the link check and the tap. After signing out, the phone enters again with the same link.
  - An organiser's *Sign out everywhere* signs the open page out live; the same phone then enters again with its link, and a copy in another browser is still refused.
  - A lost link is handled by the organiser re-sending the invitation: the open page is signed out live, the old link says it is not valid any more (even on the phone that used it), and the new link works in a new browser.
  - Gold look: without a link the stage only says to open the invitation email; the example link from a preview email shows its own message; the real link offers *Enter the arena* with nothing to type, and a tap signs in.
- **Resilience:**
  - A committed vote whose response is lost shows "Checking your vote…" with no other choice possible, then the receipt. It is stored once with one idempotency key.
  - A request that never reached the server is retried with the same key.
  - Offline: the banner shows, submission pauses, the draft is kept, and voting resumes after reconnecting.
  - A round closing while its ballot is on screen: nothing is submitted.
  - The next round opening while the previous ballot is visible: the unsubmitted choice is not transferred and the new ballot starts empty.
  - The lobby reveals a new live round without a reload, and a two-ballot event gives separate receipts.
- **Layout:** at 320, 375, 390, 430, 768 and 1440 px, the page without a link, the link page waiting for its tap, and the lobby and ballot pages have no horizontal overflow. The last nominee stays above the sticky submit bar, which stays in the viewport. A deliberately long round name wraps.
- **Accessibility:** axe-core (WCAG 2.0/2.1 A and AA rules) finds no serious or critical violations on the page without a link, the link page waiting for its tap, the "already in use" page, and the lobby and ballot pages. Voting works with the keyboard only (Tab, then arrow keys in the radio group, then Enter) with a visible focus ring. With reduced motion, the confirmation is static (the confetti canvas paints nothing) and the receipt shows the same text.

## Load tests (k6)

Run with `tests/load/run.sh --yes-synthetic-load`. Each run seeds a fresh synthetic programme in
the isolated `stagevote-load` project, then runs k6 inside the project network. CPU and RAM are
sampled every 5 s, and the stored votes are reconciled against the receipts acknowledged to
clients.

Since 2026-10-28 `seed-loadtest` gives every synthetic account a sign-in link and writes one
token per line to the accounts file (`run.sh` deletes the file when k6 finishes). The event
scenario opens `/enter` like a phone (the HTML and its files, the public programme and
`POST /api/v1/auth/link/check`) and then taps with `POST /api/v1/auth/link`. The stress mode
spends each user's first two requests on the same check and tap, so `--requests` must be at
least twice `--users`. The first section below was measured this way. **All the other load
results were measured with password sign-in**, when each virtual user signed in with
`POST /api/v1/auth/login` and the server checked an Argon2id hash. A tap is one SHA-256
lookup instead of a password hash, so their login rows and the sign-in burst described in
them belong to the old flow.

### Link sign-in (2026-10-28, 6 API workers)

These are short checks that the new sign-in works under load, not capacity measurements: they
are smaller than the password-era runs below, so compare them with care.

Event scenario: `tests/load/run.sh --yes-synthetic-load --users 200 --rounds 2 --round-seconds 30 --burst 8 --login-ramp 15`
(run `20261027T230352Z`). Each voter opens its own link, checks it, taps, opens the lobby,
holds a WebSocket and votes in both rounds, with the same duplicate (10%) and lost-response
(5%) behaviour as the main scenario.

| Measure | Result |
|---|---|
| Duration | 126 s, 6,489 HTTP requests, 800 page opens, 3,200 static files, 200 WebSocket sessions, 600 state signals delivered |
| Votes acknowledged / stored | **400 / 400 (match)**, 0 duplicate (participant, ballot) rows, 0 cross-ballot references |
| Unexpected errors / receipt mismatches | **0 / 0** |
| Link check (`POST /api/v1/auth/link/check`) | 200 of 200 answered `ready`. p50 6.0 ms, p95 10.0 ms, p99 23.5 ms |
| Link sign-in (`POST /api/v1/auth/link`) | 200 of 200 signed in. p50 14.9 ms, **p95 22.8 ms**, p99 42.7 ms, max 54.2 ms |
| Full page open (HTML and every new file) | p50 15 ms, **p95 35 ms**, p99 46 ms, max 115 ms (target: p95 < 2 s) |
| Vote submission | p50 11.5 ms, **p95 17.0 ms**, p99 22.6 ms, max 27.0 ms (target: p95 < 1 s) |
| State propagation | p50 6.7 ms, p95 11.2 ms, max 12.2 ms (target: p95 < 2 s) |
| Duplicate-retry pairs / lost responses | 40 / 20. 7 lost responses were recovered by reading the receipt and 7 by a same-key retry; all 20 later "change my vote" attempts got `409 ALREADY_VOTED`. |
| `http_req_failed` rate | 0.32% (21 requests): the 14 deliberate client timeouts and 7 receipt lookups (`404`) of the lost-response simulation |
| Peak CPU / RAM | web 91% / 127 MiB, api 67% / 509 MiB, proxy 45% / 127 MiB, db 22% / 102 MiB |

Stress mode: `tests/load/run.sh --yes-synthetic-load --users 100 --requests 3000` (run
`20261027T230656Z`). All 100 users check their link and sign in at the same moment, then share
the remaining calls.

| Requests | Failed | Average speed | p50 | p95 | p99 | Link check p95 | Link sign-in p50 / p95 / max | Votes accepted = stored |
|---|---|---|---|---|---|---|---|---|
| 3,000 | 0 | 2,188 per s (1.4 s) | 32 ms | 117 ms | 167 ms | 199 ms | 115 / 196 / 239 ms | 95 = 95 |

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
| Login (password, Argon2id) | p50 43 ms, p95 55 ms, p99 72 ms |
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
synthetic users share N API calls. With sign-in links each user's first two calls are the
link check and the tap that signs in. Each user sends its next call as soon as the previous
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
so no leak was seen. With password sign-in the slowest step was 500 sign-ins in the same
second: about 2 s typical and up to 3.4 s, because password hashing (Argon2id) is
deliberately expensive. In the short runs this login burst dominated p95 and p99. Link
sign-in has no password hash: in the smaller link run above, 100 users checking their links
and signing in at the same moment got answers within 239 ms. The 500-user runs have not been
repeated with links. These numbers are for this host; measure the production server separately.

## Other checks

| Check | How | Result |
|---|---|---|
| Link page details (2026-10-28) | A one-off Playwright script (Chromium, Pixel 7 profile) against the e2e stack, in the classic and the gold look | 24 of 24 checks passed. The token leaves the address bar and the history state when the page loads and is still gone 3 s later. A reload keeps the link (tab storage). A newer link opened in the same tab, which changes only the `#fragment`, is checked, removed from the address bar and signs in as the newer participant, leaving the older link unbound. No request URL carried a token. A token far longer than a link shows the invalid-link message. `/` in a new browser shows only where to find the link, with no button and no fields. Screenshots of every state were reviewed. |
| Invitation throughput (local Mailpit only, password invitations) | 310 invitations queued through `/admin/invitations/send` on the e2e stack | Queued in 1.6 s, including Argon2 hashing of every password (link invitations hash only a random token). All 310 were accepted by Mailpit after 140 s, which matches the default pacing of `EMAIL_MAX_PER_MINUTE=120`. 300 messages for the batch were in the inbox. |
| Backup and restore drill | `scripts/backup.sh -p stagevote-e2e`, then `scripts/restore.sh --yes-replace-database … -- -p stagevote-e2e` | Data matched before and after (21 votes, 342 participants), `stagevote reconcile` passed, `check-privileges` passed, and readiness returned ok. The first drill found a real bug: a restore with `--no-privileges` left the runtime role without grants, so readiness failed. `stagevote bootstrap` now re-applies the runtime grants every time, and `check-privileges` now also verifies the required reads and writes. The drill passed after the fix. |
| CSP | Load pages in the production build | Every script carries the per-request nonce. Pages hydrate with no CSP violations. |
| Gmail Workspace provider (key supplied by the owner for testing) | Admin › Email › Run diagnostics on the main stack | Key file readable by the app user only, sender `hello@techsarasolutions.com` configured, **token exchange refused by Google: `unauthorized_client`** (domain-wide delegation for this client ID and the `gmail.send` scope is not authorised). No email was sent. Provider not verified. |
| Production configuration refusal | `Settings.validate_for_runtime` | Refuses Mailpit, plain-text SMTP, missing SMTP credentials, non-HTTPS base URLs and example.com senders when `APP_ENV=production`. |

## Not executed or not verifiable here

- **Real-device Safari, Android Chrome on real phones, and the venue's Wi-Fi.** The WebKit and Chromium runs are emulations on one host.
- **Sign-in links inside real email apps and mail scanners.** An email app that opens links in a built-in browser of its own keeps that browser's cookies apart from Safari and Chrome, so a link first opened there is expected to be refused later in the normal browser as in use on another device. Which apps and phones do this was not tested. Company mail scanners that open or rewrite links (for example Microsoft Defender Safe Links) were not tested either. Check both on real phones and real mailboxes during the rehearsal.
- **External email providers.** These automated checks use Mailpit only. Gmail SMTP with an App Password was configured on 2026-10-20 and accepted real messages, but in the 2026-10-23 event-day test it refused bulk sending with `454` (see [email-setup.md](email-setup.md)). The Gmail API provider is configured with the owner's test key, but Google refused the delegated token (`unauthorized_client`). Inbox placement, SPF, DKIM and DMARC are not verified.
- **Production HTTPS deployment.** These checks validate `compose.prod.yaml` and the Caddy files as configuration only. The production server is deployed separately (see [deploy-ec2.md](deploy-ec2.md)) and is not part of these results.
- **Load across multiple hosts or through the internet.** The load generator and the stack share one host, so network latency is near zero. Venue Wi-Fi will add latency and loss.
