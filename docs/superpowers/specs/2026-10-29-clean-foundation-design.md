# Clean Foundation Rebuild Design

**Date:** 2026-10-29
**Status:** Proposed
**Supersedes:** All prior designs in `docs/superpowers/specs/` for the old ORB application.

## Goal

Remove the existing ORB/GTT application and rebuild a clean, trading-ready foundation in the same Git repository and remote. History is rewritten only to purge leaked credentials; the project, branch, and remote stay the same. The foundation supports authentication, Upstox connectivity, MySQL persistence, and a safe order path, but contains no trading strategy. A future strategy will be designed separately and added on top.

The rebuild must be safe for a live-money account: by default it cannot place an order, and every order that does pass through the foundation is validated, audited, and traced.

## Non-goals

- No opening-range breakout, signals, indicators, strategy worker, GTT lifecycle, backtesting, positions, P&L, or trading UI.
- No multi-user accounts, roles, or registration.
- No order polling, fill reconciliation, retries, or automatic square-off.
- No database-editable trading limits.
- No Upstox developer-app changes except those required to rotate leaked credentials.

## Decisions

| Topic | Decision |
| --- | --- |
| Repository | Same repository (`TheAmritpal/algo-orb`) and same remote; history is rewritten to purge secrets and force-pushed |
| Wipe scope | All tracked files except the new spec; `AGENTS.md` and sanitized tooling configs are recreated; `.git` survives |
| Secrets | Leaked credentials are purged from history and all affected credentials are rotated before the new app runs |
| Database | Drop and recreate `algo_orb`, single new baseline migration; tokens, users, sessions, and old strategy data are lost |
| Architecture | Layered modular monolith under `app/` |
| Safety | Single `OrderGateway` write path; live trading disabled by default; no live-order route in the foundation |
| Deployment | Hardened app-only Docker image, Compose on `127.0.0.1:3000`, host Nginx, host MySQL |

## Credential exposure and purge

The current repository tracks live credentials. The purge is part of the rebuild, not optional cleanup.

Confirmed secret-bearing paths in history:

- `.env` and `.env.production`
- `.setting/mcp-mysql.log` and `.setting/mcp-mysql-cli.log`
- `.opencode/opencode.json` — MySQL password in plain text
- `.codex/config.toml` — MySQL password in plain text

Also present on disk outside Git: `.superpowers/sdd/2026-10-16-website-login/original-index.patch` contains Upstox API key and secret. It is deleted, not backed up.

The example files `.env.example` and `deploy/production.env.example` were compared value-by-value against the real files; they contain placeholders only and do not need purging. They are still rewritten for the new configuration.

Purge process:

1. Copy `.env` and `.env.production` to a private location outside the repository with `chmod 600`. Create a local `git bundle` backup and a `mysqldump` in a gitignored `backups/` directory, also `chmod 600`; both contain secrets and are deleted after the purge is verified.
2. Rewrite all reachable history with `git filter-repo`, removing the six tracked paths listed above using `--invert-paths`. Delete the untracked `.superpowers` patch separately.
3. Run a history-wide scan for every sensitive value from the preserved `.env` files; the scan must report no matches and must print only file names, never values.
4. Force-push `main` and rewritten tags, then delete stale local branches. Existing clones and GitHub-cached commits may retain old objects; rotation, not the rewrite, is the actual protection.

Rotation checklist, performed by the account owner before the new app is used:

- Upstox sandbox and live API key/secret (regenerate in the developer console)
- MySQL application password, plus any server account that shared it
- `APP_PASSWORD`
- `APP_SESSION_SECRET`
- `UPSTOX_TOTP_SECRET` and `UPSTOX_TOTP_PIN`
- Any broker access tokens previously stored in the database (invalidated by the database drop and re-login)

`.gitignore` gains `.env*` (with `!.env.example`), `.setting/`, `backups/`, and the existing cache patterns. Tooling configs are recreated without literal credentials; where the tool supports it they reference environment variables, otherwise the MCP server entry is omitted.

## Architecture

```
app/
  main.py                  create_app(), lifespan, router mounting, logging setup
  core/
    config.py              pydantic-settings Settings, cached accessor, fail-fast validation
    logging.py             stdlib logging configuration, secret redaction filter
    security.py            Argon2 hashing, session tokens, CSRF tokens, signed OAuth state
  repositories/
    database.py            PyMySQL connection factory and transaction helper
    users.py               user lookup/upsert
    sessions.py            create/load/delete sessions, prune expired
    upstox_tokens.py       token insert/update/read per client_id + user_id
    audit.py               order_audit inserts and status transitions
  services/
    auth.py                login, logout, session validation, throttling
    upstox_auth.py         OAuth URL, state, code exchange, profile check
    risk.py                order validation against configured limits
    readiness.py           fail-closed pre-submission checks
    order_gateway.py       validate -> audit INTENT -> broker -> audit outcome
  brokers/
    base.py                OrderBroker protocol and typed request/result models
    paper.py               deterministic in-memory broker for tests and paper mode
    upstox.py              official Upstox SDK adapter
  routers/
    pages.py               /health, /login page, / dashboard
    auth.py                POST /login, POST /logout
    upstox.py              /auth/upstox/login, /auth/upstox/callback, /auth/upstox/status
  templates/               base.html, login.html, dashboard.html
  static/                  app.css
  cli/
    sync_admin.py          create/update the single admin user from env
migrations/                0001_baseline.py
tests/                     unit and route tests; fake repos and brokers
deploy/                    Dockerfile assets, compose.yaml, nginx sample, production env example
```

Dependencies: `fastapi`, `uvicorn`, `jinja2`, `pymysql`, `upstox-python-sdk`, `python-dotenv`, `pydantic-settings`, `alembic`, `sqlalchemy` (migrations only), `pwdlib[argon2]`, `python-multipart`. Development: `pytest`, `httpx`, `ruff`. Python 3.13, managed with `uv`.

Application data access uses PyMySQL through the repository layer. SQLAlchemy is used only by Alembic. Uvicorn runs one worker because login throttling and cache state are in-process.

## Configuration

`app.core.config.Settings` is a `pydantic-settings` model loaded once and cached. Missing or invalid required values fail startup with a message that names the variable and never prints its value.

App:

- `APP_ENV` — `development` or `production`
- `APP_SESSION_SECRET` — signs OAuth state and CSRF tokens
- `APP_COOKIE_SECURE` — boolean; `true` in production
- `APP_USERNAME`, `APP_PASSWORD` — consumed only by `sync_admin`
- `APP_SESSION_TTL_SECONDS` — default `43200`
- `LOGIN_MAX_ATTEMPTS` — default `5`; `LOGIN_WINDOW_SECONDS` — default `300`

MySQL: `MYSQL_HOST`, `MYSQL_PORT`, `MYSQL_USER`, `MYSQL_PASSWORD`, `MYSQL_DATABASE`.

Upstox:

- `UPSTOX_ENVIRONMENT` — `sandbox` or `live`; selects the key pair and redirect URI
- `UPSTOX_SANDBOX_API_KEY`, `UPSTOX_SANDBOX_API_SECRET`, `UPSTOX_SANDBOX_REDIRECT_URI`
- `UPSTOX_LIVE_API_KEY`, `UPSTOX_LIVE_API_SECRET`, `UPSTOX_LIVE_REDIRECT_URI`
- `UPSTOX_ACCOUNT_USER_ID` — broker account identity stored with the token

Trading:

- `TRADING_MODE` — `paper` or `live`; default `paper`
- `TRADING_ENABLED` — boolean; default `false`
- `ORDER_MAX_NOTIONAL` — decimal, required
- `ORDER_MAX_QUANTITY` — integer, required

The existing `.env` key names remain usable, but the files themselves are removed from Git and restored from the private copy after the history rewrite. Rotated values replace leaked ones, and new keys are appended by hand. Tracked example files contain placeholders only. No secret file is committed, logged, or regenerated automatically.

## Data model

All timestamps are UTC `DATETIME(3)`. One baseline migration creates four tables. Schema changes are Alembic-only; request handlers never create or alter tables.

`users`

- `id` BIGINT AUTO_INCREMENT primary key
- `username` VARCHAR(64) NOT NULL, unique
- `password_hash` VARCHAR(255) NOT NULL
- `created_at`, `updated_at`

`sessions`

- `id` BIGINT AUTO_INCREMENT primary key
- `token_hash` CHAR(64) NOT NULL, unique
- `user_id` BIGINT NOT NULL, FK to `users(id)` ON DELETE CASCADE
- `expires_at` DATETIME(3) NOT NULL, indexed
- `created_at`

`upstox_tokens`

- `client_id` VARCHAR(64), `user_id` VARCHAR(64), composite primary key
- `access_token` VARCHAR(2048) NOT NULL
- `created_at`, `updated_at`

`order_audit`

- `id` BIGINT AUTO_INCREMENT primary key
- `client_order_id` VARCHAR(64) NOT NULL, unique
- `strategy_ref` VARCHAR(64) NULL — reserved for future strategies
- `request_json` JSON NOT NULL — normalized request, no secrets
- `status` VARCHAR(16) NOT NULL, indexed — `INTENT`, `ACCEPTED`, `REJECTED`, `UNKNOWN`, `CANCELLED`
- `broker_order_id` VARCHAR(64) NULL
- `response_json` JSON NULL — sanitized broker response
- `error_code` VARCHAR(64) NULL
- `created_at`, `updated_at`

No strategy, GTT, webhook, signal, or settings tables are created.

## Authentication

- One admin user. No registration route. `sync_admin` upserts the user from `APP_USERNAME` and `APP_PASSWORD`, hashes with Argon2, logs no password, and revokes that user's existing sessions on change.
- Login verifies the hash with a constant-time comparison, emits a generic failure message, and throttles by client IP using the in-process limiter (`LOGIN_MAX_ATTEMPTS` per `LOGIN_WINDOW_SECONDS`).
- On success, create a 32-byte random token with `secrets`, store its SHA-256 hash in `sessions`, and set an `HttpOnly`, `SameSite=Lax`, `Path=/` cookie, `Secure` when configured. Expired sessions are rejected and pruned opportunistically.
- Logout deletes the session row and clears the cookie.
- Every state-changing form carries a CSRF double-submit token; mismatch is a 403.

## Upstox OAuth

1. `GET /auth/upstox/login` builds the SDK authorization URL with a short-lived HMAC-signed state, stores the state in a cookie, and redirects.
2. The callback validates signature, expiry, and cookie binding, then exchanges the single-use code through the official SDK.
3. It fetches the broker profile before storing the token. The token is upserted into `upstox_tokens` for the configured `client_id` + `UPSTOX_ACCOUNT_USER_ID`.
4. The dashboard calls `/auth/upstox/status`, which verifies the saved token against the profile API and reports `Connected`, `Disconnected`, or `Status unavailable`. Tokens and codes are never rendered, returned in URLs, or logged.
5. Failed exchange or storage is not retried automatically; the user restarts the login.

## Order path

`OrderRequest` carries `client_order_id`, `instrument`, `side`, `quantity`, `order_type`, `price`, `trigger_price`, and `product`. The gateway is the only component allowed to call a broker's write methods.

`OrderGateway.submit(request, actor)`:

1. `risk.validate` checks required fields, positive quantity, price sanity, allowed side/type/product, `quantity <= ORDER_MAX_QUANTITY`, and `quantity * price <= ORDER_MAX_NOTIONAL`. Failure raises `ValidationError` before any audit or broker call.
2. Insert `order_audit` with status `INTENT`. If this write fails, abort — no broker call.
3. `readiness.check` verifies the broker mode, valid token, `TRADING_ENABLED` for `live`, and configured limits. Failure marks the audit `REJECTED`.
4. Call the broker exactly once with `client_order_id` as the correlation tag.
5. Mark `ACCEPTED` with the broker order ID, or `REJECTED` with a sanitized error.
6. On timeout, transport error, or ambiguous response, mark `UNKNOWN` and raise `BrokerUnknown`. Never retry automatically; the caller must query order status first.

`OrderBroker` exposes only `place_order`, `get_order`, and `cancel_order`. `PaperBroker` is deterministic with injectable IDs and is used by tests. `UpstoxBroker` wraps the official SDK; SDK exceptions map to `BrokerRejected`, `BrokerUnavailable`, or `BrokerUnknown`. The foundation wires `PaperBroker` in `paper` mode and constructs `UpstoxBroker` in `live` mode, but exposes no route or CLI that submits an order. Live submission therefore requires deliberate future work.

Logging installs a redaction filter that masks `access_token`, `Authorization`, `code`, `token`, `secret`, and `password` fields in messages, exceptions, and broker payloads. Access logging stays disabled.

## Error handling

- Typed errors: `ConfigError`, `ValidationError`, `AuthError`, `UpstoxOAuthError`, `BrokerRejected`, `BrokerUnavailable`, `BrokerUnknown`.
- Routes return sanitized messages and appropriate status codes; details go to logs with secrets redacted.
- Database failures surface as a 503 page, not a stack trace. `/health` reports process liveness only and does not touch MySQL or Upstox.
- Broker uncertainty is never resolved by guessing or retrying. The audit row plus a manual/status query is the source of truth.

## Wipe and rebuild procedure

1. Pre-flight: confirm no app, worker, cron, or systemd process is running (verified 2026-10-29). Confirm the broker-side GTTs `GTT-C26281000046995` and `GTT-C26291000109789` are cancelled or settled. Copy `.env` and `.env.production` to a private path outside the repository (`chmod 600`). Create a local bundle backup in `backups/` and a `mysqldump` of `algo_orb`, both gitignored. Do not push a tag that points at history containing secrets.
2. Purge the secret-bearing paths with `git filter-repo`, scan all reachable history for the preserved secret values, and force-push `main` and rewritten tags. Delete the untracked `.superpowers` patch that contains Upstox keys.
3. Drop and recreate `algo_orb` with the existing application user, using a newly rotated password; confirm grants.
4. Commit the removal of every tracked file except this spec. Recreate `.gitignore` with `.env*` (excluding `.env.example`), `.setting/`, `backups/`, and cache patterns.
5. Recreate `AGENTS.md` with the same project constraints plus the new structure. Recreate tooling configs with no literal credentials (environment references, or no MCP entry).
6. Restore `.env` and `.env.production` from the private copy, apply rotated credentials and the new keys, and confirm both remain untracked.
7. Build the foundation in reviewable commits: scaffold/config → database + baseline migration → auth → Upstox OAuth → broker + gateway → templates/deploy → tests/docs.
8. Apply the baseline migration, run `sync_admin`, start Uvicorn, and complete the verification checklist.

## Deployment

- `Dockerfile`: multi-stage `uv` build, non-root UID 10001, no dev dependencies, `uvicorn` with one worker, no access log, proxy headers trusted only from the Compose gateway.
- `compose.yaml`: `web` service only; `read_only`, `cap_drop: ALL`, `no-new-privileges`, tmpfs `/tmp`, log rotation, healthcheck, bound to `127.0.0.1:3000:8000`, env file selected by `APP_ENV_FILE` (default `.env.production`). No worker or token-scheduler service until a strategy exists.
- `deploy/nginx/algo-orb.conf`: HTTPS termination and proxy to `127.0.0.1:3000`, access logs disabled.
- `deploy/README.md`: rewritten for the new config and migration baseline.
- `deploy/production.env.example`: new keys, placeholder values only.

## Testing

- `ruff check` and `pytest` must pass.
- Unit tests cover config validation, password/session/CSRF helpers, risk limits, gateway status transitions, audit behavior, and broker error mapping.
- Route tests use FastAPI's test client with fake repositories and a fake broker.
- Optional MySQL integration tests use `algo_orb_test`, are marked `mysql`, and skip when the database or credentials are absent.
- A migration test upgrades an empty test database, asserts the four application tables plus `alembic_version`, and downgrades cleanly.
- No test contacts Upstox or any network service.

## Definition of done

- Tracked tree contains only the new foundation and this spec; no ORB/strategy code or references remain.
- `git log --all --diff-filter=A --name-only` contains no addition of `.env`, `.env.production`, `.setting/`, or the original credential-bearing tooling files; the history-wide secret scan reports zero matches.
- `.gitignore` excludes `.env*` (except `.env.example`), `.setting/`, and `backups/`; no literal credential exists in a tracked file.
- The operator confirms all credentials in the rotation checklist have been rotated.
- Fresh database migrates to one baseline revision; `alembic current` is clean.
- `ruff check` and `pytest` pass; the optional MySQL suite passes when enabled.
- `sync_admin` is idempotent and revokes sessions on credential change.
- App boots; `/health` returns 200; login, logout, session expiry, and CSRF rejection behave as specified.
- Upstox OAuth round-trip works with real credentials; the dashboard reports connection status without exposing tokens.
- A paper order through `OrderGateway` produces `INTENT → ACCEPTED` and enforces both limits; an over-limit request produces `REJECTED`/`ValidationError` with no broker call.
- Live submission is refused while `TRADING_ENABLED=false` or readiness fails.
- `docker compose config` validates and the production image builds.

## Risks

- **Leaked credentials remain dangerous until rotated:** rewriting history does not invalidate a key. GitHub may retain old objects, caches, forks, or clones. Rotation of every listed credential is mandatory and is the real mitigation.
- **Broker-side exposure:** deleting this code does not cancel GTTs or positions held at Upstox. The operator must verify the account is flat before the wipe; the new foundation cannot manage old records.
- **Credential loss is avoided, data loss is not:** preserved env files and rotated values let the app reconnect, but dropping the database deletes saved tokens and sessions, so a fresh Upstox login is required.
- **No live route by design:** the first strategy must add an explicit, reviewed entry point; nothing can trade accidentally after the rebuild.
- **In-process throttling:** login limits reset on restart and are not shared across replicas; the deployment remains single-worker, single-replica.
- **Tooling MCP access:** the sanitized `.opencode`/`.codex` configs may omit the MySQL MCP server until the owner supplies credentials through the environment.
