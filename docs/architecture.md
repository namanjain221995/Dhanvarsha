# StageVote architecture

StageVote runs live polling for one programme of sequential rounds (about 14) with 300+
invited participants, most on phones. This note explains the moving parts and the rules
that protect votes. Code references are relative to the repository root.

## Components

```text
            phones / laptops (same origin, HTTPS in production)
                          │
                    Caddy reverse proxy ── /media/* ── uploaded images and videos (volume)
              ┌───────────┼──────────────┐
              │           │              │
         Next.js web   FastAPI API     FastAPI API      (2+ processes)
         (UI only)     /api/v1, /ws    /api/v1, /ws
                          │              │
                          └──── PostgreSQL 17 ────┐   LISTEN/NOTIFY + outbox
                                   │              │
                              worker (email, schedule, deadline sweep, maintenance)
                                   │
                         Mailpit (dev) / SMTP / Gmail API (Workspace delegation)
```

| Piece | Path | Role |
|---|---|---|
| Web | `apps/web` | Next.js 16 App Router, React 19, Tailwind 4. No API routes of its own; every page renders per request so a per-request CSP script nonce applies (`src/proxy.ts`). |
| API | `apps/api/src/stagevote` | FastAPI, SQLAlchemy 2.1 (asyncpg), Alembic. Typed request models, machine-readable errors with request IDs. |
| Worker | `stagevote worker` | Durable jobs in PostgreSQL: email delivery, scheduled opens, deadline closing, lease recovery, secret purging. Several workers may run; coordination is in the database. |
| Proxy | `infra/caddy` | Same-origin routing (`/api/*`, `/ws`, `/ws/admin`, `/media/*`, everything else to Next.js), security headers, request size limit, automatic HTTPS in production. |
| Database | PostgreSQL 17 | The only source of truth. Two roles: `stagevote_owner` (migrations) and `stagevote_app` (runtime, cannot UPDATE/DELETE/TRUNCATE votes or the audit log). |

No Redis, no queue broker, no microservices: PostgreSQL provides the locks, the job
queue (`FOR UPDATE SKIP LOCKED` + leases) and the cross-process signal (LISTEN/NOTIFY).

## Data model

Defined in `apps/api/src/stagevote/models.py`; constraints and triggers in
`apps/api/alembic/versions/0001_initial_schema.py` and the later migrations beside it.

- `programmes` – one active programme (partial unique index on `is_active`). Branding,
  timezone, login window, run mode (manual/scheduled), schedule revision and suspension,
  `state_version` (anything participants can see changed) and `live_version` (live control
  changed; used for stale-action detection).
- `events` → `ballots` → `nominees` – each event has one ballot by default and may have
  several named ballots. On team ballots a nominee is a team, and `nominees.members` (a JSONB
  array of names) lists who is in it for display only; votes reference the nominee (the team). Planned window (`planned_start_at/end_at`) is separate from the
  effective window (`opened_at`, `voting_deadline_at`, `closed_at`).
- `participants` – programme-scoped email (unique, case-insensitive key), a random
  `user_code` (`SV-…`, the participant's label in the console and the audit log; nobody
  types it), and the sign-in link state added by migration 0006: `link_token_hash` (SHA-256
  of the personal link token, unique as `uq_participants_link_token_hash`),
  `link_issued_at`, `link_device_hash` (SHA-256 of the device cookie of the browser the link
  is bound to) and `link_bound_at`. `activated_at` = first sign-in; the check
  `ck_participants_activation` allows it only on a row with a link (or, for rows from
  before 0006, a password hash). `password_hash` still holds the hashes from invitations
  sent before links and is no longer read. It, the `temp_password_*` columns and the
  `recovery_tokens` table are retired and unused (kept so existing databases need no
  destructive migration and the previous version can still run during a rollout).
- `participant_sessions`, `admin_sessions` – opaque random tokens, only SHA-256 hashes stored.
- `votes` – the receipt (`receipt_code`), idempotency key and payload hash, DB-clock
  `created_at`.
- `email_jobs` (+ `email_attempts`), `schedule_jobs`, `outbox_events`,
  `audit_log`, `rate_limit_buckets`, `worker_heartbeats`, `assets`.

### Database-enforced invariants

| # | Invariant | Enforcement |
|---|---|---|
| 1 | Email and user code unique | `UNIQUE(programme_id, email_normalized)`, `UNIQUE(user_code)` |
| 2 | One vote per participant per ballot | `UNIQUE(participant_id, ballot_id)` |
| 3 | Nominee belongs to the ballot | composite FK `(ballot_id, nominee_id) → nominees(ballot_id, id)` |
| 4 | No crossing of programme/event/ballot/participant/session | composite FKs `(event_id, ballot_id)`, `(programme_id, event_id)`, `(programme_id, participant_id)`, `(participant_id, session_id)` |
| 5 | At most one OPEN event per programme | partial unique index `WHERE status = 'open'` |
| 6 | One device per sign-in link | the binding is a single column, `participants.link_device_hash`, written only while the participant row is locked `FOR UPDATE`, so two first taps from different browsers cannot both bind; `uq_participants_link_token_hash` ties every link to one participant. Sessions stay separate rows (migration 0005 removed the one-session index): a new sign-in on the bound device revokes that participant's older live sessions, and votes stay one per ballot through invariant 2 |
| 7 | Votes immutable | runtime role has only `SELECT, INSERT` on `votes`; trigger rejects `UPDATE`/`DELETE`/`TRUNCATE` for every role; every FK to votes' parents is `ON DELETE RESTRICT`; the initial migration refuses to downgrade while votes exist |
| 8 | Overlapping planned windows rejected | `EXCLUDE USING gist (programme_id WITH =, tstzrange(...) WITH &&)`, deferrable |
| 9 | Aggregates reconcile to stored votes | results are always `COUNT` over `votes` (indexed by `(ballot_id, nominee_id)`); there is no second tally |

## The vote transaction and the cutoff

`apps/api/src/stagevote/services/voting.py`

Lock order, followed by every code path (`state.py` docstring):

1. **Programme live lock** – transaction-scoped advisory lock keyed by the programme id.
   Votes take it **shared** (votes never block each other). Live transitions – open,
   close, start-next, deadline change, scheduled open, deadline sweep, schedule resume,
   settings changes to the access window – take it **exclusive**.
2. Programme row, 3. event/ballot rows (transitions and editors only),
4. participant row (`FOR SHARE` in votes; `FOR UPDATE` in link sign-in, invitations,
   disable and session reset), 5. session row.

Nothing that holds a later lock waits for an earlier one, so these paths cannot deadlock.
Event, ballot and nominee editors take the live lock first like transitions do, and lock
rows with `FOR NO KEY UPDATE`, which does not conflict with the key-share locks that vote
foreign-key checks take.

**Acceptance point.** Inside one transaction, after acquiring locks 1, 4 and 5, the vote
reads `clock_timestamp()` (wall-clock time *after* any lock wait, not the transaction start)
and checks: session not revoked or expired, participant enabled and activated, programme
access window open, event `open`, `now < voting_deadline_at`, ballot in event, nominee in
ballot. If it passes, the vote row (with `created_at = that instant`), its receipt, the
idempotency record and an outbox row are inserted, and the transaction commits. Only then
is a receipt returned.

Because a close/start-next waits for the exclusive lock, every in-flight vote either
committed before the close (and is counted) or is evaluated after it (and is refused with
`VOTING_CLOSED`). A vote that waited on the lock past the deadline is refused because the
clock is read after the wait (tested in the backend suite).

**Idempotency.** Every submission carries an `Idempotency-Key`. The server answers
replays before checking whether voting is still open, so a retry after the round closed
still gets the original receipt.

| Situation | Result |
|---|---|
| same key, same ballot and nominee | `200` with the original receipt (`replayed: true`) |
| same key, different payload | `409 IDEMPOTENCY_KEY_REUSED`, nothing changes |
| new key, ballot already voted | `409 ALREADY_VOTED` with the stored receipt |
| two identical requests at the same instant | the unique index serialises them; one `201`, one `200` |

**Client behaviour** (`apps/web/src/components/participant/use-voting.ts`): the receipt
is shown and the celebration plays only after a `201`/`200`. A network error, timeout or
5xx moves the ballot to *Checking your vote*: the client reads the receipt; only if none
exists does it resend the *same* request with the *same* key. Pending attempts survive a
reload (sessionStorage). An unsubmitted selection is a draft and is never sent
automatically.

## Sessions and authorisation

`apps/api/src/stagevote/services/auth.py`, `api/participant.py`, `deps.py`,
`apps/web/src/app/enter/page.tsx`

- Opaque 256-bit tokens in `HttpOnly`, `SameSite=Lax` cookies (`__Host-` prefix and
  `Secure` over HTTPS). Only token hashes are stored. Nothing goes in localStorage.
- Unsafe methods require an allowed `Origin` (or `Referer`) **and** an `X-CSRF-Token`
  that is an HMAC of the session id. WebSockets require the session cookie and an allowed
  `Origin`. The two sign-in link endpoints run before any session exists, so like the
  organiser login they check the `Origin` only.
- **Personal sign-in links.** Voters have no password. Each invitation carries the link
  `{APP_BASE_URL}/enter#token=<token>`: the token is random (`security.new_token()`, 256
  bits) and the database keeps only its SHA-256 (`participants.link_token_hash`). The token
  sits in the URL fragment, which browsers never send to a server or put in a `Referer`, so
  no server sees it in a URL; it reaches the API only in the body of the two POSTs below.
  The parameter is named `token=` so that log redaction also catches it in free text. The
  `/enter` page reads it once, keeps it in this tab's sessionStorage (`svlink:token`) until
  it is used, and removes it from the address bar with `history.replaceState`, so the
  browser's copy or share action does not pass on a working link.
- **Opening is not signing in.** On load the page calls `POST /api/v1/auth/link/check`,
  which only reports the link's state (`ready`, `signed_in`, `in_use`, `invalid` or
  `disabled`). It never binds, never creates a session and writes no audit entry. Only a tap
  on the page's button (*Enter the arena* on the gold look, *Sign in* on the classic one)
  calls `POST /api/v1/auth/link`, so a mail scanner or link preview that only opens the link
  cannot use it up. A browser already signed in as that participant goes straight to the
  lobby. Without a token the page asks `GET /api/v1/auth/session`: a signed-in browser goes
  to the lobby, any other is told to open the button in their invitation email. There are
  no email or password fields anywhere on the participant side.
- **One device per link.** A device is one browser's cookie store, recognised by the device
  cookie (`__Host-sv_device` over HTTPS, `sv_device` otherwise; `HttpOnly`, `SameSite=Lax`,
  `Path=/`, a random value that expires `DEVICE_COOKIE_DAYS` after it is set, default 180).
  The check, and a tap, set it when the browser has none, so a retry after a lost response
  carries the same cookie. The first successful tap stores the SHA-256 of that cookie as
  `participants.link_device_hash` with `link_bound_at`, and audits `participant.link_bound`
  (with the browser's user agent, never a token). The tap locks the participant row
  `FOR UPDATE` before it reads or writes the binding, so of two first taps from different
  browsers exactly one binds. A tap from any other browser (a forwarded or copied link,
  another app's built-in browser on the same phone, a new private window) is refused with
  `LINK_IN_USE` and audited as `participant.link_refused`. The device cookie on its own
  grants nothing: only the session cookie authenticates requests and WebSockets, and tabs
  and reloads in the bound browser share its session. One browser may hold the links of
  several people (a shared phone); it holds one session at a time, and tapping another
  person's link switches to them.

A tap is checked in this order. Every refusal above the device row leaves the link unbound,
so tapping before sign-in opens binds nothing.

| Check | Refusal |
|---|---|
| Origin allowed | `403 BAD_ORIGIN` |
| Per-IP failure limit | `429 RATE_LIMITED` |
| The token belongs to a link of the active programme; re-checked under the participant lock in case a new invitation replaced it meanwhile | `401 LINK_INVALID` ("This sign-in link is not valid any more. Open the newest invitation email, or ask the help desk to send you a new one."), counted as an IP failure when no link matches. Malformed, unknown, replaced and other-programme tokens all get this same answer. The `#token=example` link in preview emails is never valid, and the page says it is an example |
| Account enabled | `403 ACCOUNT_DISABLED` |
| Sign-in window open, by database time | `403 LOGIN_NOT_OPEN` (with `opens_at`) or `403 LOGIN_CLOSED` |
| Link unbound, or bound to this browser's device cookie | `403 LINK_IN_USE` ("This link is already in use on another phone or browser. …"), audited, not counted as an IP failure |

- **Coming back on the same device.** Signing out (`POST /api/v1/auth/logout`) clears only
  the session cookie, never the device cookie, so the bound browser comes back by opening
  the same email again and tapping the button: after signing out, after its session
  expired, or after an organiser's *Sign out everywhere*. A tap from a browser that still
  holds a live session of that participant reuses it (`reused_session: true`). Otherwise the
  tap revokes the participant's other live sessions (reason `replaced`, signalled live like
  every revocation), creates a new session, sets `activated_at` on the first sign-in and
  audits `participant.login` with `via: link`. Votes stay one per ballot whichever session
  sends them: a repeat gets `409 ALREADY_VOTED` with the same receipt.
- **A new invitation is the only way to move a link.** Every way of inviting (*Send
  invitation* or *Resend invitation* in the participant dialog, *Send invitations to*, the
  bulk invite and the import with invitations) goes through
  `services/invitations.issue_invitations`. It makes a new token, clears the binding
  (`link_device_hash`, `link_bound_at`) and revokes every live session of that participant
  (reason `credentials_reissued`), which signs open pages out live; the old link answers
  `LINK_INVALID` from then on. The new link binds to whichever browser taps it first, so it
  covers a changed or lost phone, a link opened in the wrong app, a link someone else used
  first and a lost email. *Sign out everywhere* (`admin_reset`) ends every session but keeps
  the binding, so the same browser can come back with the same link. Disabling an account
  ends its sessions and keeps the link and its binding: after *Enable account* the same
  browser can come back. For development and tests, `stagevote sign-in-link --email
  ADDRESS` (refused in production) replaces a participant's link in the same way and prints
  it instead of emailing it.
- A sleeping phone or a dropped socket does not end the session; WebSocket presence and
  session lifetime are independent. Sessions expire after `SESSION_TTL_HOURS` (default 12),
  or are ended by logout, an organiser reset, account disabling, a re-sent invitation or a
  newer sign-in on the same device.
- Revocation is pushed to every API process through the outbox (`session` topic, naming
  the revoked session ids so a newer valid session keeps its socket), which closes those
  sockets with code 4401; every HTTP request re-checks the
  session, sockets re-check it every 60 s, and a vote re-checks it under lock.
- **Invitation-only accounts.** An organiser imports email addresses and sends invitations.
  There is no activation step, no self-service sign-up and no self-service recovery (the
  public activation and recovery endpoints, and the former password route
  `POST /api/v1/auth/login`, do not exist). Trade-off: whoever taps the button first owns
  the link. If the email is forwarded before the voter uses it, if the link is first opened
  in an email app's built-in browser and later in Safari or Chrome, or if the browser's
  cookies are cleared, the voter needs a new invitation from the help desk.
- Rate limits live in PostgreSQL (shared by all processes). For voters only failures count:
  every malformed or unknown token, on the check and on a tap, counts against the per-IP
  key `login_fail:ip:<ip>` (`LOGIN_FAILURES_PER_IP`, 1,000 per `LOGIN_IP_WINDOW_SECONDS`,
  10 minutes), which is generous because a whole venue may share one address. `LINK_IN_USE`
  is not counted, so a forwarded link cannot lock a venue out. A 256-bit token cannot be
  guessed, so voters have no per-account lockout. Organisers still sign in at
  `/admin/login` with an email address and a password (Argon2id), with their own limits.
- Every admin endpoint depends on an admin session; participant sessions cannot reach
  them. Admin views show completion (ballots voted), never individual selections.

This is an accountable, account-based voting system, **not** a cryptographically
anonymous election: the database links each vote to a participant so that duplicates can
be prevented and receipts reissued. Access to raw tables must be restricted accordingly.

## Live state, schedule and manual override

`apps/api/src/stagevote/services/live.py`

- **Manual mode**: organisers open a round with an explicit effective deadline (default:
  programme access close), close it, extend the deadline, or *start next* (atomically
  close the current round and open the selected one).
- **Scheduled mode**: saving any planned window bumps the programme's `schedule_revision`;
  pending jobs of older revisions become `stale` and new `open` jobs are created. The
  worker executes due jobs under the exclusive live lock; a job is idempotent and only
  opens an event that is still `upcoming`, never reopens a closed event, and checks the
  revision, the run mode and the override flag first.
- **Manual override**: any manual live action in scheduled mode sets
  `schedule_suspended`. Scheduled jobs are skipped until an organiser presses *Resume
  schedule*, which shows a preview, bumps the revision and regenerates jobs from the
  current time.
- **Restart behaviour**: a job that comes due after its window ended is recorded as
  `missed` (audited, shown to admins) and the event stays upcoming; it never opens an
  expired ballot. Deadlines are enforced by the vote transaction itself, so a late worker
  only delays the displayed status, never accepts a late vote.
- Every live action carries the `live_version` the organiser saw; a mismatch returns
  `409 STALE_STATE`, so two organisers cannot undo each other by accident.

## Real-time updates and recovery

`apps/api/src/stagevote/live/hub.py`, `apps/web/src/lib/live.ts`

1. Writes insert an `outbox_events` row in the same transaction as the change. A trigger
   calls `pg_notify`, which PostgreSQL delivers **only if the transaction commits** — a
   rolled-back vote never notifies.
2. Each API process keeps one `LISTEN` connection and pushes small signals to its own
   sockets: `state` (with the new `state_version` and a timestamp), `tally` (admins only,
   coalesced to once per second), `email`, `session` (revocation).
3. Signals are hints. Clients always refetch an authorised snapshot over HTTP, with
   0–700 ms jitter so a full venue does not refetch in the same millisecond.
4. Recovery: each process polls the outbox every 3 s for rows it did not see (a lost
   notification costs at most a few seconds); after its listener reconnects it tells every
   client to resync; clients reconnect with exponential backoff and jitter, treat 45 s of
   silence as a dead socket, poll every 15 s while disconnected, refetch on tab
   visibility and on `online`, and confirm state every 60 s regardless.
5. While the client cannot confirm the current state (offline, or no successful refresh
   for 25 s without a socket), voting controls are disabled and a calm reconnecting banner
   explains why.

The participant structure (events, ballots, nominees) is cached per process keyed by
`state_version`; receipts and time-dependent status are computed per request.

## Email

See [email-setup.md](email-setup.md). Jobs are durable rows; workers lease them with
`SKIP LOCKED`, record an attempt **before** calling the provider, and classify outcomes
as accepted, transient (retry with exponential backoff and jitter), permanent (no retry;
configuration errors pause sending for 5 minutes) or unknown (the connection broke after
the message was handed over — never retried automatically). The invitation's link token
is Fernet-encrypted in the job (`{"link_token": …}`) with `INVITATION_ENCRYPTION_KEY` so a
retry sends the same link, and purged once the job is accepted, superseded or older than
`INVITATION_RETRY_HOURS`. After that only the hash is left, so nobody, organisers
included, can see an existing link again; it can only be replaced. A superseded invitation
is never sent with a stale link, and a job queued before migration 0006 with a password in
its payload fails as `retired_payload` instead of being sent. Preview and test emails carry
the example link `{APP_BASE_URL}/enter#token=example`, which signs nobody in. The
Gmail provider uses a separate HTTP connection per send (httplib2 is not thread-safe).

## Uploads

Images are checked against a 40-megapixel limit (250 for opening-page media) before decoding, then decoded with Pillow, EXIF-rotated,
resized, re-encoded to WebP, stored under random names, and served by Caddy with
`nosniff` and immutable caching. SVG and other non-raster formats are rejected.

The image or video an opening-page card shows full screen (asset kind `opening_media`,
`services/media.py`) can be far larger than one request may be (Cloudflare: 100 MB, Caddy:
12 MB), so it is uploaded in 8 MiB chunks:

1. `POST /api/v1/admin/media/uploads` with the name and size checks the size limit and the
   free disk space and creates `<media root>/.uploads/<id>.part` and `<id>.json`.
2. `PUT /api/v1/admin/media/uploads/<id>?offset=<n>` appends the raw body at `n`, which must
   be the size stored so far (otherwise 409 with that size, so the browser resumes). A lock
   on the `.part` file serialises the requests of one upload, and any API process can serve
   any of them.
3. `POST .../<id>/complete` reads the type from the file's first bytes. Images get the
   WebP treatment above with a 3840 px long edge; MP4 and WebM videos are never decoded and
   are renamed into the media root as they are (`<random>.mp4` or `.webm`).

Caddy serves only `<32 hex>.webp|mp4|webm` names from the media root, with Range support,
`nosniff` and `Content-Security-Policy: default-src 'none'; sandbox`, so `.uploads/` is
unreachable and no uploaded file can run as a page.

## Security headers and limits

Caddy: `X-Content-Type-Options`, `Referrer-Policy`, `X-Frame-Options: DENY`,
`Permissions-Policy`, `Cross-Origin-Opener-Policy`, HSTS in production, 12 MB request
cap. Next.js: per-request CSP with script nonces and `strict-dynamic`, `connect-src`
limited to the same origin, `frame-ancestors 'none'`. API: 2 MB JSON cap, JSON logs with
redaction and no query strings or bodies, SQL statement parameters never logged
(`hide_parameters`), FastAPI's built-in telemetry disabled.
Private pages and API responses are `no-store`; there is no service worker.

## Decisions and trade-offs

- **PostgreSQL LISTEN/NOTIFY instead of Redis.** One authoritative store; NOTIFY is
  transactional; the outbox gives a durable record for catch-up. NOTIFY payloads are
  small (< 8 KB) and the channel is not a durable queue, which is why clients treat it as
  a hint and rely on snapshots.
- **Advisory live lock instead of row locks on a hot row.** Shared advisory locks scale
  across hundreds of concurrent votes without rewriting a heavily contended tuple.
- **Personal links instead of voter passwords.** A tap replaces typing an email address and
  a password on a phone, and a 256-bit token is checked with one SHA-256 lookup, so a
  venue-wide sign-in burst costs no password hashing. Binding each link to the first browser
  that uses it stops forwarded or copied links from working once the owner has entered. The
  price: a new phone, another browser or cleared cookies need a new invitation from the help
  desk, and a link someone else taps first is theirs until the invitation is re-sent.
- **Argon2id at OWASP's 19 MiB / t=2 profile** for organiser passwords, with a concurrency
  semaphore: strong enough, and a burst of sign-ins cannot exhaust memory.
- **Next.js renders every page per request** so CSP nonces work; pages are light and the
  data is client-fetched, so this costs little.
