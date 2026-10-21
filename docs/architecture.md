# StageVote architecture

StageVote runs live polling for one programme of sequential rounds (about 14) with 300+
invited participants, most on phones. This note explains the moving parts and the rules
that protect votes. Code references are relative to the repository root.

## Components

```text
            phones / laptops (same origin, HTTPS in production)
                          │
                    Caddy reverse proxy ── /media/* ── uploaded images (volume)
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
`apps/api/alembic/versions/0001_initial_schema.py`.

- `programmes` – one active programme (partial unique index on `is_active`). Branding,
  timezone, login window, run mode (manual/scheduled), schedule revision and suspension,
  `state_version` (anything participants can see changed) and `live_version` (live control
  changed; used for stale-action detection).
- `events` → `ballots` → `nominees` – each event has one ballot by default and may have
  several named ballots. On team ballots a nominee is a team, and `nominees.members` (a JSONB
  array of names) lists who is in it for display only; votes reference the nominee (the team). Planned window (`planned_start_at/end_at`) is separate from the
  effective window (`opened_at`, `voting_deadline_at`, `closed_at`).
- `participants` – programme-scoped email (unique, case-insensitive key; it is the user ID
  participants type), a readable `user_code` (also accepted), the Argon2id hash of the
  password from the latest invitation, and `activated_at` = first sign-in. The
  `temp_password_*` columns and the `recovery_tokens` table are retired and unused (kept so
  existing databases need no destructive migration).
- `participant_sessions`, `admin_sessions` – opaque random tokens, only SHA-256 hashes stored.
- `votes` – the receipt (`receipt_code`), idempotency key and payload hash, DB-clock
  `created_at`.
- `email_jobs` (+ `email_attempts`), `schedule_jobs`, `outbox_events`,
  `audit_log`, `rate_limit_buckets`, `worker_heartbeats`, `assets`.

### Database-enforced invariants

| # | Invariant | Enforcement |
|---|---|---|
| 1 | Email and user ID unique | `UNIQUE(programme_id, email_normalized)`, `UNIQUE(user_code)` |
| 2 | One vote per participant per ballot | `UNIQUE(participant_id, ballot_id)` |
| 3 | Nominee belongs to the ballot | composite FK `(ballot_id, nominee_id) → nominees(ballot_id, id)` |
| 4 | No crossing of programme/event/ballot/participant/session | composite FKs `(event_id, ballot_id)`, `(programme_id, event_id)`, `(programme_id, participant_id)`, `(participant_id, session_id)` |
| 5 | At most one OPEN event per programme | partial unique index `WHERE status = 'open'` |
| 6 | One vote per participant per ballot, however many devices they use | unique `(participant_id, ballot_id)` on `votes`. Since migration 0005 a participant may hold several live sessions (one per device); the earlier one-session index is gone |
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
4. participant row (`FOR SHARE` in votes; `FOR UPDATE` in login, invitations, disable
   and session reset), 5. session row.

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

`apps/api/src/stagevote/services/auth.py`, `deps.py`

- Opaque 256-bit tokens in `HttpOnly`, `SameSite=Lax` cookies (`__Host-` prefix and
  `Secure` over HTTPS). Only token hashes are stored. Nothing goes in localStorage.
- Unsafe methods require an allowed `Origin` (or `Referer`) **and** an `X-CSRF-Token`
  that is an HMAC of the session id. WebSockets require the cookie and an allowed `Origin`.
- **Several devices per participant.** A voter may be signed in on a phone and a laptop at
  the same time; each sign-in creates its own session. Login locks the participant row
  `FOR UPDATE`, revokes that participant's expired sessions in the same transaction, and
  reuses the session when the same browser signs in again. Tabs and reloads that share the
  cookie keep working. Votes stay one per ballot whichever device sends them: a second
  device gets `409 ALREADY_VOTED` with the same receipt. Logging out ends only that device's
  session. IP addresses are not used (a venue shares one public IP).
- A sleeping phone or a dropped socket does not end the session; WebSocket presence and
  session lifetime are independent. Sessions expire after `SESSION_TTL_HOURS` (default 12),
  or when ended by logout, an organiser reset, account disabling or a re-sent invitation.
- Revocation is pushed to every API process through the outbox (`session` topic, naming
  the revoked session ids so a newer valid session keeps its socket), which closes those
  sockets with code 4401; every HTTP request re-checks the
  session, sockets re-check it every 60 s, and a vote re-checks it under lock.
- **Invitation-only accounts.** An organiser imports email addresses and sends invitations.
  Each invitation generates a random password (three groups of four characters, about 59
  bits), stores only its Argon2id hash as the account password, and emails the user ID —
  the participant's email address — with that password. The participant signs in
  directly; there is no activation step, no self-service sign-up and no self-service
  password reset (the public endpoints do not exist). If someone loses the email or is
  locked out, an organiser re-sends the invitation: a new password, the old one stops
  working, and any open session is signed out. Trade-off: the emailed password is the
  long-term password, so anyone who can read that mailbox can sign in; the single-session
  rule and organiser re-sends limit the impact.
- Rate limits live in PostgreSQL (shared by all processes). Each attempt is counted
  against the typed identifier *before* the password is checked (atomic increment and
  compare), so parallel guesses cannot exceed 8 per 15 minutes; a successful sign-in
  clears the counter. The key is the same whether or not the account exists, and unknown
  or not-yet-invited accounts still cost one Argon2 verification, so neither responses
  nor timing reveal who is a participant. Failures per IP are capped generously (1,000
  per 10 minutes) because a whole venue may share one address.
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
the message was handed over — never retried automatically). The invitation password is
Fernet-encrypted in the job with `INVITATION_ENCRYPTION_KEY` so a retry sends the same
password, and purged once the job is accepted, superseded or older than
`INVITATION_RETRY_HOURS`. A superseded invitation is never sent with a stale password. The
Gmail provider uses a separate HTTP connection per send (httplib2 is not thread-safe).

## Uploads

Images are checked against a 40-megapixel limit before decoding, then decoded with Pillow, EXIF-rotated,
resized, re-encoded to WebP, stored under random names, and served by Caddy with
`nosniff` and immutable caching. SVG and other non-raster formats are rejected.

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
- **Argon2id at OWASP's 19 MiB / t=2 profile** with a concurrency semaphore: strong enough,
  and a venue-wide login burst cannot exhaust memory.
- **Next.js renders every page per request** so CSP nonces work; pages are light and the
  data is client-fetched, so this costs little.
