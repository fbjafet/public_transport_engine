# Fix notes — public_transport_engine

These notes follow the fix sequence from the review: lock it down, add the service layer, fix the data-integrity items, fix the live feed, add migrations and indexes, then add tests.

## What I fixed, and why

**1. Locked the application down (review items 1–5, 11).**
Every page and the live feed now require sign-in, state-changing requests need a CSRF token and the `dispatcher` role (`viewer` accounts are read-only), and sessions end when a password changes or an account is deactivated. The live feed rendered server text with `innerHTML`, which allowed script injection from driver notes or model output; it now uses `textContent`, and a Content-Security-Policy blocks inline scripts as a second layer. The app also refuses to start in production with a missing, short or placeholder `SECRET_KEY`, the debugger is off unless explicitly enabled in development, and the hard-coded database credentials are gone. I did this first because nothing else matters while anyone on the network can publish passenger alerts.

**2. Moved the business logic into a service layer (item 18).**
`services/disruption_service.py` now owns create, regenerate, publish and resolve, and the route handlers only parse input, call it, broadcast and redirect. The draft-creation code that was duplicated in two handlers exists once, and the rules live in a pure, database-free `services/state.py` so they can be tested on their own. I did this before the logic fixes so each rule is fixed in one place instead of twice.

**3. Fixed the data-integrity problems (items 6–10, 12–15).**
Alerts now follow `draft → published → archived`. Publishing is idempotent, archived drafts and alerts on resolved disruptions are refused with a 409, and publishing a new version supersedes the live one, backed by a unique database index so two live versions cannot exist. Route status is no longer overwritten: it is recomputed as the worst status among the route's open disruptions, so a minor incident cannot downgrade a suspension and resolving one of two incidents leaves the right status. Model output is validated and clamped (bad `route_status`, wrong types, over-length SMS and signage text, non-object JSON), and failures are logged and labelled "template" instead of "AI" in the UI. Model calls run with no transaction open, writes for a route are serialised by row locks taken in a fixed order, pages shown after a write read from the primary database, duplicate form submissions are collapsed, and stops are checked against the chosen route.

**4. Fixed the live feed (item 11).**
A client that falls behind is now disconnected cleanly instead of being dropped from the list while its connection stayed open and silent, so the browser reconnects. Events carry ids and a short history is kept, so a reconnecting browser receives what it missed, or reloads if the gap is too large. Connections are capped in total and per user, and the "new disruption" event no longer carries unreviewed model text. I also added `gunicorn.conf.py`, which pins one worker process because the broker lives in process memory.

**5. Added Alembic migrations and indexes (items 20, 22).**
`create_all` at startup is replaced by a baseline migration of the original schema plus a second migration. The second adds the users table, the new disruption columns, the missing indexes, `ON DELETE` rules on the foreign keys, and the one-live-alert-per-channel index. It also recomputes existing route statuses and archives duplicate published alerts so the new constraints can apply to existing data. I did this before any real data exists because retrofitting schema control later is much harder.

**6. Added tests and CI (step 6).**
The suite covers the state rules, model-output validation, the live-feed broker, config checks, the auth gate, CSRF and roles, plus full request flows including escaping, idempotency, state conflicts, derived route status and row-lock concurrency. A GitHub Actions workflow runs it on SQLite and on PostgreSQL, and checks that the migrations apply, round-trip and match the models. This makes "tested before production" something the branch enforces rather than a promise.

## What I am knowingly leaving, and why

**"Published" still only means a database flag and a live-feed event.** Nothing is sent to an SMS gateway, signage controller or public website, because no delivery integration exists in the code and I would be guessing at your providers. Real delivery should be added as a separate step after the commit, with its own retries and failure state.

**The live feed and the rate limiters are in-process.** They are correct for one worker process (what `gunicorn.conf.py` configures) and wrong for several, because each process would see only some clients and only its own counters. Scaling out needs Redis or PostgreSQL `LISTEN/NOTIFY`. I did not add that infrastructure for a single-instance app.

**Rate limiting depends on seeing real client addresses.** Behind a reverse proxy, set `TRUSTED_PROXY_COUNT` or every user shares the proxy's address and the login limits trigger for everyone. I left that as a documented deployment setting rather than guessing your network layout.

**Account management is minimal.** Users are created and passwords changed with the `flask` CLI. There is no admin screen, self-service reset, multi-factor sign-in, or audit trail of who published what; the alerts table does not record the publishing user. Those are product decisions that need your input, and the CLI is enough for a small dispatcher team to start.

**Prompt injection is contained, not eliminated.** The note is delimited as data, output is validated, and the model cannot rate an incident below what a keyword check found. Over-warning on a false keyword match is the deliberate trade-off. A person still reviews every draft before it is published, and that review is the real control.

**Resolving a disruption archives drafts but leaves published alerts as history.** They disappear from the dashboard feed, but with no delivery integration there is nothing to withdraw. Once delivery exists, resolving should send a "service restored" message.

**I did not restructure the package layout.** `models`, `routes`, `services` and `seeds` still sit beside `app`, and the database engines are still created at import time. Moving them would touch every import in the project for no security or correctness gain; I only made the read engine reuse the write engine when there is no replica. Fix it as its own change once the tests are running in CI.

**Read-after-write protection is time-based.** After a write, that user's reads go to the primary for 30 seconds. It does not guarantee a different user sees the change on a lagging replica.

**Smaller items.**
- No dependency lockfile or vulnerability scan; versions are bounded ranges.
- Seed trip times are relative to "now" and go stale.
- Status badge colours in the CSS still repeat the literals from the models.
- Lists are not paginated, which is fine for a small network.
- TLS and HSTS are expected to come from your proxy.

**Not verified in the environment where I wrote this.** SQLAlchemy and PostgreSQL could not be installed there. The 35 database-backed tests (including the concurrency tests), both migrations and the `alembic check` drift check have not been run. Run the CI workflow on the branch and read its result before merging.
