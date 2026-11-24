# Transit Disruption & Notification Engine

Flask app for logging transit disruptions, drafting passenger alerts with an LLM (with a template fallback), and publishing them to a live dispatcher feed. See `FIX_NOTES.md` for what changed and what is deliberately left.

## Local development

```bash
python -m venv .venv && . .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements-dev.txt
cp .env.example .env                                 # then fill in SECRET_KEY and DATABASE_URL
python -c "import secrets; print(secrets.token_urlsafe(48))"   # use as SECRET_KEY

alembic upgrade head                                 # or set AUTO_CREATE_SCHEMA=1 for throwaway dev
python -m seeds.seed_data                            # optional demo data
flask --app run create-user alice --role dispatcher  # prompts for a 12+ character password
python run.py                                        # http://127.0.0.1:5000
```

Roles: `dispatcher` can log, regenerate, publish and resolve; `viewer` is read-only.

## Upgrading an existing database

The old app created tables with `create_all`. Take a backup, then:

```bash
alembic stamp 0001      # tell Alembic the existing schema is the baseline
alembic upgrade head    # users table, new columns, indexes, ON DELETE rules
```

`0002` recomputes every route's status from its open disruptions and archives any duplicate published alerts on the same channel. Review the result on a copy first.

## Tests

```bash
python -m pytest -q                                   # SQLite, no setup
TEST_DATABASE_URL=postgresql+psycopg://postgres:postgres@localhost:5432/transit_test python -m pytest -q
```

The test database name must contain `test`; the suite wipes its tables. Row-lock concurrency tests run only on PostgreSQL. Tests never use `DATABASE_URL` from `.env`.

CI (`.github/workflows/ci.yml`) runs the suite on SQLite and PostgreSQL, then checks that migrations apply, match the models (`alembic check`) and round-trip.

## Before production

- [ ] CI is green on the branch, including the PostgreSQL job
- [ ] Migration rehearsed on a copy of production data
- [ ] `APP_ENV=production` (or unset), a random `SECRET_KEY` of 32+ characters, a dedicated database user
- [ ] Served over HTTPS; behind a proxy set `TRUSTED_PROXY_COUNT` (otherwise login rate limits count the proxy's address)
- [ ] Run with `gunicorn -c gunicorn.conf.py run:app`, **one worker process** (the live feed is in-process)
- [ ] Dispatcher and viewer accounts created; no default accounts exist
- [ ] LLM key set (or intentionally empty to use templates) and `LLM_API_URL` is https
- [ ] Manual pass: sign in, log a disruption, regenerate, publish, resolve; confirm a viewer cannot write; confirm the feed updates in two browsers

## Layout

```
app/            config, db, security (auth/CSRF/limits/headers), consistency, errors, cli
models/         constants (single source of enums), transit, user
services/       state (pure rules), ai_service, disruption_service, sse_service
routes/         auth, dashboard, disruptions (thin HTTP layer)
migrations/     0001 baseline, 0002 security/state/indexes
templates/ static/ seeds/ tests/
```
