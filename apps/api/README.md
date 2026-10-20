# StageVote API and worker

FastAPI application, domain services, Alembic migrations, background worker and operator
CLI for StageVote. See the repository [README](../../README.md) for setup, and
[docs/architecture.md](../../docs/architecture.md) for the vote transaction, lock order,
sessions, schedule and live updates.

```sh
. ../../scripts/host-env.sh     # point at the Compose PostgreSQL on 127.0.0.1:55432
uv sync
uv run pytest -q                # integration tests against a throwaway PostgreSQL database
uv run ruff check src tests && uv run mypy
uv run stagevote --help
```

Layout:

```text
src/stagevote/
  main.py            app factory, middleware, error handlers
  config.py          settings and production safety checks
  models.py          SQLAlchemy models (constraints in alembic/versions/0001_initial_schema.py)
  state.py           lock order, advisory live lock, outbox, audit
  deps.py            origin/CSRF checks and session authentication
  security.py        Argon2id, tokens, CSRF tokens, Fernet payloads, password policy
  ratelimit.py       PostgreSQL-backed fixed-window limits
  api/               routers (participant, admin_*)
  services/          voting, auth, live control and schedule, snapshots, results, invitations, imports, uploads
  live/              per-process WebSocket hub fed by LISTEN/NOTIFY and outbox catch-up
  mailer/            providers (SMTP/Mailpit, Gmail API), templates, durable delivery
  worker/            background worker loops
  cli.py, seed.py    operator commands and opt-in demo/load-test data
tests/               integration tests (real PostgreSQL, two-process live test)
```
