# Algo ORB

FastAPI application with Jinja2 templates, PyMySQL, and the
official Upstox Python SDK. Uses Python 3.13 and uv.

## Setup

```bash
uv sync --frozen
test -e .env || cp .env.example .env
# Fill in MYSQL_*, APP_* and Upstox settings before running migrations.
uv run alembic upgrade head
uv run python -m app.cli.sync_admin
uv run uvicorn app.main:app --reload --no-access-log
```

For an existing installation, edit your current `.env`; preserve existing credentials.

Open http://localhost:8000 for the login page. `/health` reports application
liveness only. FastAPI's Swagger UI, ReDoc, and OpenAPI schema endpoints are disabled.

`uv.lock` pins compatible dependency versions for reproducible installs.
Package installation requires access to PyPI or a populated uv cache.

## MySQL

Install or provision MySQL separately, create the `algo_orb` database and an
application user, then update the `MYSQL_*` values in `.env`. The Python driver
does not install a MySQL server or create databases.

For authentication that requires RSA support, install the optional dependencies
with `uv add 'pymysql[rsa]'` when network access is available.

`app.repositories.database.get_connection()` opens a connection only when called. Close it
after use. Missing settings raise a configuration error; connector failures
propagate to the caller. The login page and public health route do not require a running database. Signing in and protected routes require migrated MySQL tables.

## Database migrations

Alembic manages the schema using the `MYSQL_*` settings from `.env`.
Credentials are not stored in `alembic.ini`. SQLAlchemy is used for migrations;
application token writes continue to use PyMySQL.

```bash
uv run alembic upgrade head
uv run alembic current
```

Revision `0001` creates `upstox_tokens` with the existing composite primary key,
ASCII identifier columns, access token, and timestamp behavior. Run migrations
before starting the OAuth login flow. A migration account needs schema-change
privileges; the running application only needs INSERT and UPDATE for token writes.

If the table already exists from the earlier callback implementation, compare
`SHOW CREATE TABLE upstox_tokens` against the initial migration before adopting
it. Only if the schema matches, use `uv run alembic stamp 0001` to record the
existing schema without recreating the table. Do not stamp a missing or
different schema, and do not drop an existing table to run the initial migration.

Create subsequent migrations with `uv run alembic revision -m "describe change"`
and edit their `upgrade()` and `downgrade()` functions. These are explicitly
authored migrations; ORM metadata/autogeneration is not configured.

Preview the initial MySQL SQL without connecting to the database:

```bash
uv run alembic upgrade head --sql
```

The initial revision's downgrade drops `upstox_tokens` and deletes its token data.

## Upstox

The SDK imports as `upstox_client`. Set `UPSTOX_API_KEY` and `UPSTOX_API_SECRET`
in `.env`, along with:

```dotenv
UPSTOX_REDIRECT_URI=http://localhost:8000/auth/upstox/callback
```

Register exactly that redirect URL in your app's settings at
[Upstox Developer Apps](https://account.upstox.com/developer/apps).
Changing `.env` does not change the Upstox dashboard configuration.

1. Sign in at http://localhost:8000/login, then open
   http://localhost:8000/auth/upstox/login in your browser, or click
   **Connect Upstox** on the home page. Use `localhost` consistently so the
   login cookie is sent to the callback.
2. Complete login on Upstox. Upstox redirects to `/auth/upstox/callback`
   with a single-use authorization code and state.
3. The callback verifies the signed state and browser cookie, exchanges the
   code through the SDK, and saves the token before redirecting to the home page.
   The token stays in MySQL and is not returned in the browser response or URL.
   Responses disable caching and clear the state cookie.

The dashboard reads the most recently connected account for the configured Upstox
app and verifies its saved token through the SDK's read-only profile API on each
page load. A valid token shows **Connected** and the broker account ID, with the
connect button hidden. Missing or rejected credentials show **Disconnected** and
**Connect Upstox**. Database or temporary broker failures show **Status unavailable**
without claiming the account is disconnected. Tokens remain in MySQL and are never
rendered in the page.

The callback inserts or updates the migrated `upstox_tokens` table's
current token by `(client_id, user_id)`. The table includes `access_token`,
`created_at`, and `updated_at`. The configured MySQL database must exist and the
schema must be migrated before login. Access tokens are
stored as sensitive database values; restrict access to this table and backups.
OAuth does not read or update `UPSTOX_ACCESS_TOKEN` in `.env`.

Login state expires after 10 minutes. Invalid state, missing codes, broker
failures, and database failures return sanitized errors. After an exchange or
storage failure, fix the cause and start login again: Upstox codes are single-use.
The application does not automatically retry token exchange.

Keep `.env` and real credentials out of source control. The run command disables
Uvicorn access logging so callback authorization codes do not appear in request
logs. Use the same policy for any reverse proxy. Use HTTPS when deploying beyond
localhost; the state cookie is marked Secure for HTTPS redirect URLs.

The web application handles login and broker authorization. Order execution belongs to the separate RELIANCE worker described below.

## Tests

```bash
uv run python -m unittest discover -v
```

Tests simulate broker and database boundaries without using live credentials.

Development and tests never submit live orders. Live deployment requires explicit configuration and authorization under AGENTS.md.

## Project structure

```text
app/
  main.py              # FastAPI application composition
  core/                # Environment settings, security, template loader
  routers/             # Website, login/logout, and Upstox HTTP routes
  services/            # Authentication policy and broker workflows
  repositories/        # MySQL connections, users, sessions, broker tokens
  cli/sync_admin.py    # Explicit administrator provisioning command
  templates/           # Shared layout, login page, dashboard
  static/              # Local styles; no external frontend dependencies
migrations/versions/   # Alembic schema revisions
tests/                 # Mocked database and broker tests
```

## Website login

Configure these in your local `.env` (which is ignored by Git):

```dotenv
APP_USERNAME=admin
APP_PASSWORD=replace_with_your_password
APP_SESSION_SECRET=replace_with_a_random_secret_of_at_least_32_characters
APP_COOKIE_SECURE=false
```

Replace both placeholder secrets before use. Choose a strong, unique password.
Generate the session secret with a password manager or a cryptographically secure
random generator, save it directly in `.env`, and do not paste it into logs or
source control. Existing environment variables take precedence over `.env`.
`APP_COOKIE_SECURE` defaults to true; false is for local HTTP development only.
Use HTTPS and `APP_COOKIE_SECURE=true` when deploying.

Revision `0002` creates the MySQL `users` and `sessions` tables. After configuring
MySQL, run the migrations and explicit account synchronization:

```bash
uv run alembic upgrade head
uv run python -m app.cli.sync_admin
```

The account stores an Argon2 password hash. The sync command creates the first
administrator or updates the existing administrator with the same username and
revokes all of that account's sessions. Editing `.env` alone does not change the
persisted account: rerun the command to rotate the password. A different existing
username is rejected; rename it explicitly through database administration before
syncing the renamed account. Do not delete an account just to change its name.
Concurrent provisioning commands serialize using a MySQL named lock.

Open `/login` to sign in. The dashboard and both Upstox authorization routes require
a valid website session. Sessions expire after eight hours, and signing out deletes
the server-side session. Browser cookies are HttpOnly and SameSite=Lax; only a hash
of each browser session token is stored in MySQL. Login and logout forms enforce
CSRF checks. If a website session expires during Upstox authorization, sign in and
restart the broker connection flow. `/health` remains public and reports liveness,
not database readiness. API documentation endpoints remain disabled.

The built-in login limiter permits five failed attempts per client in five minutes,
counts in-flight requests, and bounds client bookkeeping. Run one application
process; configure a shared limiter at the gateway or in shared storage before
running multiple workers or replicas. Raw forwarded headers are ignored by the
limiter. Uvicorn may rewrite the ASGI peer address when proxy headers are enabled:
trust only your actual proxy using `--forwarded-allow-ips`, or disable proxy headers
with `--no-proxy-headers`. Never trust forwarded client addresses from the public
internet. Disable access logging on Uvicorn and the reverse proxy to keep broker
callback authorization codes out of request logs.

The migration account needs schema-change privileges, including REFERENCES
for the sessions-to-users foreign key. The runtime account needs
SELECT, INSERT, UPDATE and DELETE on the authentication tables, plus the documented
broker token privileges. The database must already exist. Runtime handlers never
create tables. Authentication/database failures return sanitized errors and fail
closed. Restrict database and backup access because sessions and broker tokens are
sensitive. Downgrading revision `0002` removes website accounts and sessions.

### Completing an interrupted MySQL authentication migration

MySQL commits DDL separately. If revision `0002` creates `users` but fails before
creating `sessions`, keep the existing table. The revision validates any existing
authentication tables against its expected columns, defaults, keys, and engine
before reusing them; it refuses mismatched schemas instead of dropping data.
Do not stamp revision `0002` while sessions or its expiry index are missing.

If the migration account lacks REFERENCES, a MySQL administrator must grant it.
Use the database and MySQL account configured in `.env`, with the account host
from MySQL's account definition (which may differ from `MYSQL_HOST`):

```sql
GRANT REFERENCES ON `your_database`.* TO 'your_mysql_user'@'account_host';
```

Then complete setup from this project directory:

```bash
uv run alembic upgrade head
uv run python -m app.cli.sync_admin
uv run uvicorn app.main:app --reload --no-access-log
```

For this checkout, the generated website username/password and session secret are
stored only in local `.env`. Read the APP_USERNAME and APP_PASSWORD values there
to sign in after completing setup; keep that file private. Existing broker and
MySQL settings were preserved.

## RELIANCE strategy worker

The strategy worker runs separately from Uvicorn. It defaults to paper execution;
website login, broker connection, and opening the dashboard do not start it.
Set `ORB_BROKER_USER_ID` to the connected account ID, then run migrations explicitly
before starting `uv run python -m app.cli.run_strategy`. Paper mode still needs a
valid pinned account token for read-only live market data; all fills are simulated.

Only one worker per broker app/account/mode can hold the MySQL advisory lock.
Use an external process supervisor and keep the worker available through 15:25 IST.
SIGINT/SIGTERM stops the loop and preserves unresolved state. Stopping the worker
does not close NRML positions or arrange a future broker square-off. On restart,
existing fills and orders are reconciled before considering new entries.

### Strategy behavior

The worker marks RELIANCE equity's 09:15–09:30 IST range and waits for a later
completed 15-minute candle to close strictly outside it. The earliest signal is
09:45. Above the range buys a call; below buys a put. It uses the nearest actual
monthly expiry and the closest listed strike strictly ITM at entry. Strike,
expiry and units come from current Upstox metadata; two lots is not two units.
The instrument is resolved by RELIANCE equity's ISIN key `NSE_EQ|INE002A01018`,
and returned option metadata must identify that underlying and RELIANCE symbol.

Entries use NRML (`product="D"`), a price-bounded LIMIT order, and one durable
entry allowance per selected account, mode and exchange date. Rejected attempts
consume that allowance. Partial entry fills are protected and unfilled remainder
is cancelled after at most a 30-second observation window, subject to broker
confirmation; no top-ups or re-entry. A stop fill, target hit, rejected protection
or off-tick fill average cancels the pending remainder immediately before
closing confirmed exposure. Old signals are not replayed after restart.
The last-entry cutoff is 15:00 IST. Holidays and special sessions are skipped.

Stop and target refer to the actual option fill average: entry minus ₹10 and
entry plus ₹20. This is a trigger policy, not a guaranteed realized loss cap.
Live protection uses a broker-held stop-limit order. The worker monitors the
target and initiates the time exit at 15:25 IST. It confirms stop cancellation
and intervening fills before submitting a replacement sell. Limit/stop-limit
orders may remain unfilled after gaps or during illiquidity. Exposure remains
visible until fills confirm closure. NRML itself does not provide a broker
intraday auto-square-off guarantee.

### Settings and deployment

`ORB_MODE=paper` and `ORB_LIVE_ENABLED=false` are the defaults. Set the pinned
broker account using `ORB_BROKER_USER_ID`; this does not fall back to whichever
account connected most recently. Paper execution uses real read-only quotes and
deterministic simulated fills. Triggered stop-limit state survives gap rebounds
and restarts, and stale quotes cannot produce fills. It does not model queue priority, market impact,
fees, or realistic partial fills. Paper/live run histories are separate.

For any future live deployment, all five `ORB_MAX_*` amounts must be finite,
positive rupee values. `PREMIUM` caps unit premium; `NOTIONAL` caps total entry
premium (`price × units`); `PLANNED_LOSS` caps the planned `₹10 × units` risk;
`SPREAD` caps ask-minus-bid; `SLIPPAGE` bounds the sell-limit price below the
observed bid and protective trigger. Buy limits use the quoted ask, not an
unbounded market order. Quote freshness defaults to 5 seconds and polling to
2 seconds. Configuration must match available premium funds and selected lots.
No real monetary limits have been selected or written to local credentials.

`ORB_PROTECTIVE_STOP_VERIFIED=false` blocks live entries until acceptance of
protective stop-limit orders has been established in a broker sandbox for the
intended product. Mock tests verify SDK payloads, not broker/account eligibility.
A broker can still reject protection after a fill; the worker then reconciles
and attempts to close confirmed exposure. Do not set the flag based on tests
alone. The implementation does not issue a live probe to establish eligibility.

Migration revision `0003` adds strategy runs, intents, unique trade fills and
sanitized events. Preview without a database connection:

```bash
uv run alembic upgrade head --sql
```

Apply migrations explicitly during deployment, using a migration-capable account:

```bash
uv run alembic upgrade head
```

The runtime account needs SELECT/INSERT/UPDATE on strategy tables, SELECT on
upstox_tokens, and permission to use MySQL advisory locks. Schema changes never
run from HTTP handlers. Downgrading 0003 deletes strategy records and must not
be used to reset a daily entry allowance or to repair an open position.
Enabling execution, applying migrations and starting a worker are separate
operations. This development change does not perform them against live services.

### SDK boundary and recovery

Checked against installed `upstox-python-sdk==2.28.0` and the local reference
checkout `/home/amritpal/Downloads/upstox-python-master/`. The installed public
V3 class is `OrderApiV3`; its source filename in the reference is
`order_controller_v_3_api.py`. Execution consistently uses the supported V2
`OrderApi` interface. Broker tick metadata is converted from paise to rupees.
See the official [option contracts](https://upstox.com/developer/api-documentation/get-option-contracts/)
and [place-order](https://upstox.com/developer/api-documentation/place-order/)
references. Shared SDK requests are throttled to fewer than six per second
per worker and have bounded timeouts; transport retries are disabled. MySQL
connection/read/write timeouts are five seconds. Transient market reads before
reservation use bounded retry backoff without consuming the entry allowance.
Avoid simultaneous paper/live workers on the same account because broker rate
limits are shared across processes and the web application.

An order intent and unique tag are saved before submission. An accepted request
is not a confirmed fill. After a timeout, the worker searches the broker order
book by ID/tag and requires consistent trade evidence and position quantity.
Unknown outcomes are not blindly resubmitted. A missing old order, expired token,
manual trade in the same contract, database outage or inconsistent fill data
keeps the run in reconciliation_required and blocks new entries.

Keep manual positions out of the strategy's selected contract. Other instruments
are not closed by this worker, and it never calls an unfiltered exit-all API.
If an exit is unresolved, inspect the broker order/trade book and actual position
first; do not restart with a different account, delete records, or resubmit an
uncertain order. Restore the original account token and database/worker access,
then restart to reconcile. Token changes take effect on worker restart. If the
broker cannot supply sufficient historical evidence, resolve exposure through
the broker and reconcile the stored records explicitly with an operator rather
than assuming a fill. Previous-day unresolved runs block new entries.

The strategy dashboard is read-only. It shows mode, range, contract, fill average, premium
levels, remaining units, daily allowance and worker heartbeat. A heartbeat older
than 30 seconds is stale. Neither a connected broker nor a running web server
means the trading worker is running.

### Separate Upstox sandbox and live apps

Apply `uv run alembic upgrade head` before using the updated login flow. Revision
0004 preserves existing tokens as `live`, keys tokens by app/account/environment,
and adds `sandbox_order_tests` and append-only `sandbox_test_events` for test
requests and sanitized response states. Strategy records already separate paper
and live runs by their mode. Sandbox probes use dedicated tables and never enter
live strategy records. Downgrading 0004 is intentionally blocked to preserve
credentials and test evidence.

The existing `UPSTOX_API_KEY`, `UPSTOX_API_SECRET`, and `UPSTOX_REDIRECT_URI`
configure the live app. Alternatively configure `UPSTOX_LIVE_API_KEY` and
`UPSTOX_LIVE_API_SECRET` together, plus `UPSTOX_LIVE_REDIRECT_URI`. A partial new
credential pair fails instead of mixing credentials from two apps.

Configure the separate sandbox app using `UPSTOX_SANDBOX_API_KEY`.
Sign into the website and use **Connect Upstox** for the live app. Live callbacks
validate signed, expiring state bound to the browser cookie, exchange the code,
and store the returned token in the live environment. Application access logs
strip query strings from Upstox authentication routes.

For the sandbox, use **Open sandbox app** to visit the dedicated
[Sandbox Apps section](https://account.upstox.com/developer/apps#sandbox), open
your app, and select **Generate** / **Generate access token**. The current
Upstox frontend uses its developer-portal sandbox token endpoint rather than the
live OAuth dialog; trying the sandbox app key at the live dialog returned
`UDAPI100068` in interactive testing. The sandbox button opens the portal instead
of attempting that unsupported flow. Save the generated token with the
dashboard's password-style token field or:

```bash
uv run python -m app.cli.sandbox import-token
uv run python -m app.cli.sandbox status
```

The CLI reads imported tokens through a hidden prompt. It never takes tokens in
command-line arguments or stores them in `.env`. Set `UPSTOX_SANDBOX_USER_ID` to
pin the sandbox account. Without it, CLI tests select the latest token for the
sandbox app only; manual imports use the local identifier `sandbox`. Stored-token
status does not imply broker verification. Changing the identifier selects a
different token row.

For an explicitly requested sandbox order probe:

```bash
uv run python -m app.cli.sandbox test-order \
  --instrument 'NSE_EQ|INE002A01018' --quantity 1 --price 10 --tick-size 0.05
```

Verify the chosen instrument's actual tick size before testing. The probe sends
one delivery LIMIT BUY and then requests cancellation using the official SDK's
`Configuration(sandbox=True)`. It cannot switch to the live endpoint. Defaults
limit it to one equity unit and INR 1,000 notional; configure
`UPSTOX_SANDBOX_MAX_QUANTITY` and `UPSTOX_SANDBOX_MAX_NOTIONAL` separately from live
strategy limits. It persists the request before submission, serializes probes
per app/account, and disables mutation retries. Pending, uncertain and
cancellation-acknowledged responses block further probes
for that account until an operator has reconciled the sandbox outcome. Because
current sandbox APIs cannot establish final order state, an acknowledgement does
not automatically clear this block. After independently confirming the result
through Upstox, an operator must explicitly reconcile the stored test record;
there is no automatic reset or blind retry. Responses are
recorded as acknowledgement states and validated order IDs; raw SDK errors,
authorization headers, and secrets are never stored in the audit.

`cancel_acknowledged` means Upstox accepted the cancellation request, not that a
fill or final cancellation was confirmed. The SDK's current sandbox allowlist
covers place/modify/cancel, but not profile, market data, positions, order books,
or trades. Therefore full strategy execution still uses `ORB_MODE=paper` for
simulated fills with live read-only market data, or explicitly enabled
`ORB_MODE=live`. `ORB_MODE=sandbox` fails closed; use the sandbox probe for order
integration tests. Live OAuth login and profile checks do not enable live orders.

Opt-in MySQL integration checks use uniquely named fake fixtures, make no broker
calls, and remove their records afterward:

```bash
ORB_MYSQL_TESTS=1 uv run python -m unittest tests.test_mysql_environments -v
```
