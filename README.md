# Algo ORB

FastAPI application with Jinja2 templates, PyMySQL, and the
official Upstox Python SDK. Uses Python 3.13 and uv.

## Setup

```bash
uv sync --frozen
cp .env.example .env
# Fill in MYSQL_* and Upstox settings before running migrations.
uv run alembic upgrade head
uv run uvicorn app.main:app --reload --no-access-log
```

Open http://localhost:8000 for the home page. `/health` reports application
liveness only. FastAPI's Swagger UI, ReDoc, and OpenAPI schema endpoints are disabled.

`uv.lock` pins compatible dependency versions for reproducible installs.
Package installation requires access to PyPI or a populated uv cache.

## MySQL

Install or provision MySQL separately, create the `algo_orb` database and an
application user, then update the `MYSQL_*` values in `.env`. The Python driver
does not install a MySQL server or create databases.

For authentication that requires RSA support, install the optional dependencies
with `uv add 'pymysql[rsa]'` when network access is available.

`app.database.get_connection()` opens a connection only when called. Close it
after use. Missing settings raise a configuration error; connector failures
propagate to the caller. The starter routes do not require a running database.

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

1. Open http://localhost:8000/auth/upstox/login in your browser, or click
   **Connect Upstox** on the home page. Use `localhost` consistently so the
   login cookie is sent to the callback.
2. Complete login on Upstox. Upstox redirects to `/auth/upstox/callback`
   with a single-use authorization code and state.
3. The callback verifies the signed state and browser cookie, exchanges the
   code through the SDK, and saves the token before redirecting to the home page.
   The token stays in MySQL and is not returned in the browser response or URL.
   Responses disable caching and clear the state cookie.

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

This implements login and token persistence only; it does not submit orders.

## Tests

```bash
uv run python -m unittest discover -v
```

Tests simulate broker and database boundaries without using live credentials.

Live trading requires explicit implementation and authorization under AGENTS.md.
