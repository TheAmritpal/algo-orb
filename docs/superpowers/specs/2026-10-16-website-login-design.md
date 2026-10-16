# Website login and project structure

Date: 2026-10-16
Status: Design approved in conversation; written specification awaiting review.

## Purpose and scope

Protect the existing Algo ORB website with a username/password login. Keep the FastAPI, Uvicorn, uv, Jinja2, MySQL, Alembic, and official Upstox SDK stack. Preserve the existing OAuth and token persistence behavior behind the website login. Do not implement or submit trading orders.

## Project layout

Keep `app/main.py` as the application composition entry point. Group configuration and security utilities in `app/core/`, HTTP handlers in `app/routers/`, authentication and broker workflows in `app/services/`, and MySQL access in `app/repositories/`. Keep Jinja2 pages under `app/templates/` and styles under `app/static/`. Retain the root `migrations/` and `tests/` directories. Update existing imports, tests, and Alembic configuration as modules move. Preserve current staged work.

## Account configuration and provisioning

Use `APP_USERNAME` and `APP_PASSWORD` in `.env` for the single administrator account. Add placeholders to `.env.example`; never print, commit, or overwrite existing secrets. Use an explicit provisioning command, `uv run python -m app.cli.sync_admin`, after migrations to create or update the configured account. Re-running the command synchronizes the configured account credentials and revokes its existing sessions. Username changes require an explicit operator decision about the prior account; the command must refuse to silently create a second administrator when a different account already exists.

Store only a salted password hash in MySQL using a maintained password-hashing implementation. Do not store plaintext passwords in the database or include credentials in logs or errors. Login checks the persisted hash, so editing `.env` requires rerunning the documented provisioning command. Validate nonempty credentials and reject sample placeholder passwords during provisioning. No registration, password-reset, or user-management interface is included.

## Database schema

Create a new Alembic revision after `0001`. The `users` table has an integer primary key, a unique case-sensitive username, a password hash, and creation/update timestamps. The `sessions` table has a primary key, user foreign key with cascading deletion, a unique SHA-256 hash of a cryptographically random session token, a CSRF token, creation timestamp, and indexed expiration timestamp. Browser session tokens themselves are never stored in MySQL. All application queries use parameters and connections are closed reliably. Schema changes happen only through Alembic.

## HTTP and session behavior

`GET /login` renders a Jinja2 form. `POST /login` validates form size and credentials, uses a generic invalid-credentials message, and performs dummy password verification for unknown usernames to reduce timing differences. Failed attempts receive a bounded per-client rate limit; this initial deployment supports one application process and documents that shared limiting is required before multiple workers are deployed. Successful login issues a new random session, clears the pre-login cookie, and redirects to `/`. No user-controlled external redirect is accepted.

Use a signed, short-lived pre-login CSRF challenge protected by `APP_SESSION_SECRET` and verify it for login submissions. Reject missing or malformed CSRF tokens. Generate independent session CSRF tokens and require them for logout and subsequent state-changing website forms.

Use an HttpOnly, SameSite=Lax session cookie scoped to `/`, expiring after eight hours. `APP_COOKIE_SECURE` defaults to true; local HTTP development explicitly sets it false. An expired, malformed, revoked, or unknown token does not authenticate. `POST /logout` deletes the server-side session and clears the browser cookie. Protected responses and authentication pages use `Cache-Control: no-store`.

Protect `/` and both Upstox OAuth routes with the same authentication dependency before broker or token database work. Unauthenticated requests redirect to `/login`; losing the session during OAuth requires logging in and restarting the Upstox flow. Keep `/health` public with only its existing liveness response, and allow static assets needed by the login page. Keep API documentation disabled.

## Failure behavior

Missing or invalid authentication configuration fails closed. Database failures produce a sanitized service-unavailable response and never authenticate a request. Authentication errors never reveal whether a username exists. Logging must omit passwords, cookies, authorization codes, access tokens, and SQL values. Preserve the existing Upstox state validation and safe failure handling.

## Interface

Provide a simple responsive login page with username, password, submit button, and a clear generic error area. Add a CSRF-protected logout form to the authenticated home page. Keep existing dashboard content and the Connect Upstox action.

## Verification and setup

Use mocked MySQL and Upstox boundaries for tests. Cover successful and failed login, password hashing, unknown-user verification, rate limiting, CSRF rejection, session cookie attributes, session expiry and revocation, logout, unauthenticated dashboard and OAuth access, database failures, credential synchronization, and secret-free responses. Update existing OAuth tests to supply a valid website session while retaining their broker-flow assertions. Check migration SQL for the new tables without connecting to MySQL, and run the complete existing test suite.

Document `.env` settings, dependency synchronization with uv, Alembic migration, account provisioning, and Uvicorn startup. Applying migrations to a configured MySQL instance and provisioning the account are part of setup; report clearly whether they were actually run. No tests may submit live orders.
