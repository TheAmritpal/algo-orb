# Website Login Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Organize the existing application and protect its website and broker authorization flow with an administrator login provisioned from `.env`.

**Architecture:** Keep HTTP handlers thin, authentication workflows in services, security primitives and configuration in core, and parameterized MySQL operations in repositories. Alembic owns schema creation. Persist password hashes and hashed opaque session identifiers; use browser cookies to authenticate protected routes.

**Tech Stack:** Python >=3.13, FastAPI, Jinja2, PyMySQL, MySQL, Alembic, uv, Uvicorn, official Upstox Python SDK; add `pwdlib[argon2]` and `python-multipart` through uv.

**Spec:** `docs/superpowers/specs/2026-10-16-website-login-design.md` (user approved).

## Global Constraints

- Keep the FastAPI, Uvicorn, uv, Jinja2, MySQL, Alembic, and official Upstox SDK stack.
- Do not implement or submit trading orders.
- Preserve current staged work.
- Schema changes happen only through Alembic.
- Browser session tokens themselves are never stored in MySQL.
- Use an HttpOnly, SameSite=Lax session cookie scoped to `/`, expiring after eight hours.
- `APP_COOKIE_SECURE` defaults to true; local HTTP development explicitly sets it false.
- Keep `/health` public with only its existing liveness response, and allow static assets needed by the login page.
- Keep API documentation disabled.
- Logging must omit passwords, cookies, authorization codes, access tokens, and SQL values.

## Review Focus

- Malformed or oversized forms, repeated keys, non-UTF-8 inputs, and excessively long cookie values must fail safely before expensive password work (Task 4).
- Simultaneous provisioning and username changes must not create two administrator accounts or retain sessions after password rotation (Task 3).
- A valid browser session must not bypass independent Upstox OAuth state verification; expired website sessions must prevent broker calls (Task 4).
- Forged proxy headers must not bypass the client rate limit; limiter bookkeeping must stay bounded (Task 4).
- Corrupt persisted password hashes or broken database connections must not authenticate or expose exception details (Tasks 3 and 4).

## File responsibilities

- `app/main.py`: application composition, static mount, sanitized error handling and no-store response policy.
- `app/core/config.py`: existing MySQL/Upstox configuration plus website and provisioning settings.
- `app/core/security.py`: password hashing, token generation/hashing, signed pre-login CSRF helpers.
- `app/core/templating.py`: shared Jinja2 template loader with absolute directory paths.
- `app/repositories/database.py`: existing on-demand connection and migration URL functions.
- `app/repositories/tokens.py`: existing Upstox token writes.
- `app/repositories/users.py`: account lookup and transactional administrator synchronization.
- `app/repositories/sessions.py`: session creation, lookup, deletion and pruning.
- `app/services/auth.py`: login, session authentication, rate limiting, website dependencies.
- `app/services/upstox.py`: existing state, exchange, storage and sanitized-error helpers.
- `app/routers/auth.py`: login page, login submission and logout submission.
- `app/routers/pages.py`: authenticated home and public health route.
- `app/routers/upstox.py`: authenticated broker login/callback handlers.
- `app/cli/sync_admin.py`: explicit account provisioning entry point.
- `app/templates/base.html`, `login.html`, `index.html`; `app/static/app.css`: shared layout, login form, dashboard and responsive styling.
- `migrations/versions/0002_create_website_auth.py`: users and sessions schema.
- `tests/test_security.py`, `test_users.py`, `test_sessions.py`, `test_auth.py`, `test_sync_admin.py`: new mocked tests; adapt existing tests for moved modules and authenticated broker routes.
- Add `__init__.py` files to new Python packages; update `.env.example`, `README.md`, `pyproject.toml`, `uv.lock`, and `migrations/env.py`.

## Execution setup

- [ ] Read the spec and repository instructions; use the worktree skill to choose isolation that includes the user's existing staged work. Do not create a clean HEAD checkout that omits it. Capture initial status before changes.
- [ ] Run the current suite with `uv run python -m unittest discover -v` as a baseline. Never contact the live broker during verification.
- [ ] At implementation time, consult official documentation for newly used library APIs. Resolve/install dependencies through uv with required filesystem/network permissions.
- [ ] Commit only task-specific changes when compatible with preserving the existing staged work; never commit the user's staged changes incidentally. Report remaining staged/unstaged changes explicitly.

### Task 1: Organize the existing application

**Files:** New core, repositories, routers and services packages; moved existing modules; modified `app/main.py`, `migrations/env.py`, and existing tests.

**Interfaces:** Preserve `get_mysql_config() -> dict[str, str | int]`, `get_upstox_config() -> dict[str, str]`, `get_connection()`, `get_sqlalchemy_url() -> URL`, and `save_token(client_id: str, user_id: str, access_token: str) -> None` in the destinations listed above. Preserve `app.main:app`. `app/core/templating.py` exports `templates: Jinja2Templates`.

- [ ] Write import and route smoke tests in `tests/test_structure.py` asserting the new public module paths import, `/health` returns `{"status": "ok"}`, and the existing template and migration behaviors remain available. Update existing patch targets to their new defining modules.
- [ ] Run `uv run python -m unittest tests.test_structure -v`; confirm failures are missing planned modules.
- [ ] Move configuration, connection and token modules to the planned destinations; split existing OAuth route handlers and helper functions across router/service modules. Update imports and patch targets together. Extract home/health routes and shared template loader. Do not change broker behavior in this task.
- [ ] Run `uv run python -m unittest discover -v`; require all baseline and structure tests to pass. Review the diff for accidental omission of staged code.
- [ ] Commit the organization changes with message `refactor: organize application modules` without including unrelated staged edits.

### Task 2: Add authentication schema and security primitives

**Files:** `app/core/config.py`, `app/core/security.py`, `migrations/versions/0002_create_website_auth.py`, `tests/test_security.py`, `tests/test_migrations.py`, `pyproject.toml`, `uv.lock`, `.env.example`.

**Interfaces:** Configuration exports frozen `AuthSettings(secret: str, cookie_secure: bool)`, `get_auth_settings() -> AuthSettings`, and `get_admin_credentials() -> tuple[str, str]`. Security exports `hash_password(password: str) -> str`, `verify_password(password: str, encoded: str | None) -> bool`, `new_token() -> str`, `hash_token(token: str) -> str`, `new_login_csrf(secret: str) -> str`, and `valid_login_csrf(token: str, cookie: str, secret: str) -> bool`.

- [ ] Add failing security tests asserting different salted hashes for one password, correct/wrong password verification, dummy verification for absent hashes, false for corrupt hashes, 64-character SHA-256 token hashes, and CSRF rejection for tampering, mismatch, age over 600 seconds or future timestamp. Assert `APP_SESSION_SECRET` requires at least 32 characters and boolean settings reject invalid values.
- [ ] Extend migration tests to assert users/sessions tables, case-sensitive unique usernames, unique token hashes, expiry index and cascading foreign key. Assert offline downgrade `0002:0001` removes sessions before users and leaves `upstox_tokens` intact.
- [ ] Run `uv run python -m unittest tests.test_security tests.test_migrations -v`; confirm expected missing-interface/schema failures.
- [ ] Add dependencies through uv. Use pwdlib recommended Argon2 hashing; catch corrupt stored hash errors and perform dummy verification when the account is absent. Generate URL-safe 32-byte random tokens. Use timestamped HMAC-SHA256 pre-login CSRF challenges and constant-time comparison. Load secrets from the existing dotenv configuration without override or logging.
- [ ] Implement revision `0002` after `0001`: users use integer ID, case-sensitive UTF-8 username VARCHAR(128), password_hash VARCHAR(255), timestamps; sessions use integer ID, user_id, ASCII token_hash CHAR(64), ASCII csrf_token VARCHAR(64), UTC creation/expiry DATETIME, and an expiry index. Use InnoDB and matching FK column types. Add empty credential placeholders, `APP_SESSION_SECRET`, and local-development `APP_COOKIE_SECURE=false` to `.env.example` only.
- [ ] Run the targeted security/migration tests and `uv run alembic upgrade head --sql`; require tests to pass and the new DDL to be present with no credential values.
- [ ] Commit with message `feat: add website authentication schema and security`.

### Task 3: Persist accounts and sessions; provision the administrator

**Files:** `app/repositories/users.py`, `app/repositories/sessions.py`, `app/cli/sync_admin.py`, `tests/test_users.py`, `tests/test_sessions.py`, `tests/test_sync_admin.py`.

**Interfaces:** Users exports frozen `User(id: int, username: str, password_hash: str)`, `find_user(username: str) -> User | None`, `sync_admin(username: str, password_hash: str) -> int`. Sessions exports frozen `Session(user_id: int, username: str, csrf_token: str, expires_at: datetime)`, `create_session(user_id: int, token_hash: str, csrf_token: str, expires_at: datetime) -> None`, `find_session(token_hash: str) -> Session | None`, `delete_session(token_hash: str) -> None`. CLI exports `main() -> int` and exits using its return code.

- [ ] Write mocked cursor tests asserting parameter binding, no plaintext password/raw session token in queries, UTC expiration filtering, commit/rollback and close behavior, and propagation of commit failures. Assert account synchronization deletes that user's sessions in the same transaction.
- [ ] Write provisioning tests asserting insert for an empty user table, update for the same username, refusal for a different existing username, and no write for missing/placeholder credentials. Assert CLI errors omit password and SQL exception text and return nonzero.
- [ ] Add a concurrent-provisioning test asserting the synchronization acquires/releases one database-scoped MySQL named lock around its transaction, releases it on failure, and fails safely when lock acquisition times out. This serializes the explicit single-admin provisioning command even on an initially empty table.
- [ ] Run `uv run python -m unittest tests.test_users tests.test_sessions tests.test_sync_admin -v`; confirm the new interfaces fail before implementation.
- [ ] Implement repositories using short-lived parameterized PyMySQL connections and dataclass results. Acquire a database-scoped named lock for provisioning; select accounts under transaction, refuse mismatched existing usernames, update/insert, revoke sessions, commit and release the lock in finally. Use database UTC time for expiration queries and prune expired sessions during session creation. Implement `uv run python -m app.cli.sync_admin` using configured credentials and password hashing without schema creation.
- [ ] Run the targeted tests; require all transactional failure and sanitized CLI tests to pass.
- [ ] Commit with message `feat: persist administrator accounts and sessions`.

### Task 4: Implement and enforce website authentication

**Files:** `app/services/auth.py`, `app/routers/auth.py`, `app/routers/pages.py`, `app/routers/upstox.py`, `app/main.py`, templates/static files, `tests/test_auth.py`, `tests/test_upstox_auth.py`.

**Interfaces:** Auth service exports `authenticate(username: str, password: str) -> User | None`, `issue_session(user: User) -> tuple[str, str]` (raw cookie and CSRF token), `require_session(request: Request) -> Session`, and `LoginLimiter.allow(client: str) -> bool`, `LoginLimiter.failed(client: str) -> None`. `require_session` redirects absent/invalid sessions to `/login` through a dedicated exception handler; repository failures raise sanitized HTTP 503 errors. Router exports `router: APIRouter`.

- [ ] Extend the existing ASGI test helper to accept method/body/headers and retain full Set-Cookie headers. Write tests that GET `/login` provides a signed pre-login cookie and hidden CSRF token; valid POST issues a new session and redirects to `/`; wrong/unknown users return identical generic errors; logout rejects missing CSRF, deletes a valid session and clears the cookie. Assert raw session tokens are hashed before repository calls and cookie expiry is 28,800 seconds with HttpOnly, SameSite=Lax and configured Secure.
- [ ] Add tests that expired/revoked/malformed sessions redirect, database failure returns sanitized 503, `/health` remains public, and every protected route returns no-store. Check the login template escapes reflected values and has accessible labels/autocomplete fields.
- [ ] Add form tests for payload over 8 KiB (413), duplicate/missing fields, invalid UTF-8 and fields outside username 1..128/password 1..1024 characters (400). Reject malformed cookie tokens before database lookup. Never truncate passwords.
- [ ] Add deterministic limiter tests: five failures per client per 300-second window, sixth submission returns 429 with Retry-After, window expiry restores access, and X-Forwarded-For is ignored. Bound storage to 10,000 entries; if full after expired-entry pruning, reject new clients temporarily rather than evicting active limits.
- [ ] Add authorization tests asserting anonymous `/`, `/auth/upstox/login`, `/auth/upstox/callback` redirect before broker calls. Adapt existing OAuth tests with a mocked valid website session, retain all OAuth-state checks, and add expired-website-session callback coverage.
- [ ] Run `uv run python -m unittest tests.test_auth tests.test_upstox_auth -v`; confirm expected missing-route/authentication failures.
- [ ] Implement service and routes with signed pre-login CSRF, constant-time session CSRF checks, generic errors, shared limiter and short-lived DB access. Read at most 8 KiB of streamed form data before parsing; reject duplicate fields. Execute password hashing and blocking database calls off the event loop. Require the same session dependency on the dashboard and both OAuth routes. Set no-store globally for non-static responses, and sanitize authentication failures without logging exception text.
- [ ] Add shared base/login templates, responsive CSS, and the CSRF-protected POST logout form while preserving home content and Connect Upstox. Keep docs disabled, static assets public, and `/health` unchanged. Validate security configuration at authentication boundaries so `/health` can remain independent of missing settings.
- [ ] Run targeted auth/OAuth tests and then the full unittest suite; require all tests to pass without live network calls.
- [ ] Commit with message `feat: protect website and broker routes with login`.

### Task 5: Document setup and verify the integrated result

**Files:** `README.md`, `.env.example`; implementation files only if verification exposes a defect.

**Interfaces:** Document `uv run alembic upgrade head`, `uv run python -m app.cli.sync_admin`, and `uv run uvicorn app.main:app --reload --no-access-log`.

- [ ] Document all APP_* settings, generating a strong session secret without exposing it in logs, administrator provisioning/password rotation and session revocation, refusing username changes, eight-hour expiry, HTTPS Secure cookies, single-process limiter scope, proxy trust, and an external/shared limiter requirement before scaling workers.
- [ ] Document that schema migration needs DDL permissions while the runtime requires SELECT/INSERT/UPDATE/DELETE on the authentication tables. Explain that changing `.env` credentials requires rerunning provisioning; do not copy/overwrite a user's existing `.env` during setup.
- [ ] Run `uv sync --frozen`, `uv run python -m unittest discover -v`, `uv run alembic upgrade head --sql`, and `git diff --check`; require a frozen install, passing suite, complete DDL and no whitespace errors. Inspect status and diffs for secrets and preservation of original staged work.
- [ ] With user-authorized write/network permissions, apply `uv run alembic upgrade head` to the configured MySQL instance when available. Provision only when APP_* credentials are configured; do not invent a default password. Never display `.env` contents. If connection/settings are unavailable, report the exact remaining setup commands and do not claim the database was changed.
- [ ] Use verification-before-completion and requesting-code-review skills. Address review defects, rerun affected checks, then report delivered functionality, test evidence and any unexecuted database setup.
- [ ] Commit documentation with message `docs: explain protected website setup` when safe for the existing index.
