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

This implements website login, broker authorization and token persistence; it does not submit orders.

## Tests

```bash
uv run python -m unittest discover -v
```

Tests simulate broker and database boundaries without using live credentials.

Live trading requires explicit implementation and authorization under AGENTS.md.

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
