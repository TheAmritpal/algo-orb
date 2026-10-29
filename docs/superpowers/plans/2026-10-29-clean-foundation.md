# Clean Foundation Rebuild Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the existing ORB/GTT application with a clean, trading-ready FastAPI foundation that contains no strategy and cannot place live orders by default.

**Architecture:** Layered modular monolith under `app/`: core config/logging/security, PyMySQL repositories, auth/Upstox/risk/readiness services, broker adapters behind an `OrderBroker` protocol, and a single `OrderGateway` that audits before and after every broker write. Alembic owns the schema; one baseline migration creates `users`, `sessions`, `upstox_tokens`, and `order_audit`.

**Tech Stack:** Python 3.13, uv, FastAPI, Jinja2, PyMySQL, Alembic + SQLAlchemy (migrations only), pydantic-settings, pwdlib/Argon2, official Upstox Python SDK, pytest, httpx, ruff, Docker Compose, host Nginx/MySQL.

**Spec:** `docs/superpowers/specs/2026-10-29-clean-foundation-design.md`

## Global Constraints

- Read the spec before starting. It is the authority when this plan is silent.
- Python 3.13, dependencies managed only with `uv`; run the app with Uvicorn.
- MySQL is the database. Schema changes only through Alembic migrations; no table creation in request handlers.
- Use the official Upstox Python SDK for Upstox calls.
- All secrets live in environment variables or `.env*` files, never in tracked files, logs, or error messages.
- Order placement, modification, and cancellation affect real money. No live order route or CLI exists in this foundation.
- Tests use fakes and never contact Upstox or any network service.
- `TRADING_MODE` defaults to `paper`; `TRADING_ENABLED` defaults to `false`.
- A single Uvicorn worker; login throttling is in-process.
- Commit after every task. Never commit `.env*`, `.setting/`, or `backups/`.

## Review Focus

These are the failure modes most likely to hurt a live-money user; each is pinned to a test in the named task.

1. Missing or malformed configuration starts the app with a partial/unsafe setup — Task 2 must fail startup and never echo the value.
2. OAuth callback with a missing, expired, replayed, or mismatched state accepts a token — Task 7 must reject it and keep tokens/codes out of logs and URLs.
3. Login brute force, CSRF-less POST, or a stale session gets access — Task 6 must throttle, reject CSRF, and expire sessions.
4. A broker timeout or ambiguous response triggers a duplicate order — Task 9 must record `UNKNOWN`, raise, and never retry.
5. An over-limit, zero-quantity, missing-price, or MARKET order reaches the broker — Task 8/9 must reject it before any broker call.
6. MySQL is down on the dashboard — Task 6 must return a 503 page while `/health` still returns 200.

---

### Task 1: Secret purge, old tree removal, and database reset

**Files:**
- Delete: every tracked path except `docs/superpowers/specs/2026-10-29-clean-foundation-design.md` and this plan
- Delete: `.superpowers/sdd/2026-10-16-website-login/original-index.patch` (untracked, contains Upstox keys)
- Create: `.gitignore`
- Preserve outside repo: `.env`, `.env.production` at `~/.config/algo-orb/` (mode 600)

**Interfaces:**
- Consumes: approved spec; broker-side exposure confirmed clean by the operator
- Produces: a repository whose reachable history contains no `.env`, `.env.production`, `.setting/`, `.opencode/opencode.json`, or `.codex/config.toml`; an empty `algo_orb` database

- [ ] **Step 1: Gate on the operator's confirmation**

Ask and wait for an explicit yes to both before running Step 2:

1. The Upstox account is flat and GTTs `GTT-C26281000046995` and `GTT-C26291000109789` are cancelled or settled.
2. The operator accepts that local `.env*` files are copied aside and that the database will be dropped.

Do not proceed if either answer is no.

- [ ] **Step 2: Verify no process is running and preserve local secrets**

Run:
```bash
ps -eo cmd | grep -Ei 'uvicorn|run_strategy|token_scheduler' | grep -v grep || true
mkdir -p "$HOME/.config/algo-orb" && chmod 700 "$HOME/.config/algo-orb"
cp .env .env.production "$HOME/.config/algo-orb/"
chmod 600 "$HOME/.config/algo-orb/.env" "$HOME/.config/algo-orb/.env.production"
mkdir -p backups && chmod 700 backups
git bundle create backups/pre-foundation-2026-10-29.bundle --all
chmod 600 backups/pre-foundation-2026-10-29.bundle
```
Expected: no matching process; both env files copied with mode 600; bundle created.

- [ ] **Step 3: Dump the database before dropping it**

Create a mode-600 defaults file from `.env` without printing values, run `mysqldump`, then delete the temporary file:

```bash
umask 077
get() { grep -m1 "^$1=" .env | cut -d= -f2-; }
{
  echo "[client]"
  echo "host=$(get MYSQL_HOST)"
  echo "port=$(get MYSQL_PORT)"
  echo "user=$(get MYSQL_USER)"
  echo "password=$(get MYSQL_PASSWORD)"
} > /tmp/algo-orb-dump.cnf
chmod 600 /tmp/algo-orb-dump.cnf
mysqldump --defaults-extra-file=/tmp/algo-orb-dump.cnf --single-transaction --routines=FALSE --events=FALSE algo_orb > backups/algo_orb-pre-foundation.sql
rm -f /tmp/algo-orb-dump.cnf
chmod 600 backups/algo_orb-pre-foundation.sql
```
Expected: non-empty dump; temporary config removed. If the app user lacks dump privileges, stop and ask the operator for a dump account.

- [ ] **Step 4: Rewrite history to remove secret-bearing paths**

Run:
```bash
command -v git-filter-repo || uv tool install git-filter-repo
git filter-repo --force --invert-paths \
  --path .env \
  --path .env.production \
  --path .setting/ \
  --path .opencode/opencode.json \
  --path .codex/config.toml
git remote -v | grep -q origin || git remote add origin git@github.com:TheAmritpal/algo-orb.git
```
Expected: filter-repo reports rewritten commits; `git remote -v` shows origin.

- [ ] **Step 5: Scan all reachable history for the preserved secret values**

Run an inline stdlib Python scan that loads sensitive values from `~/.config/algo-orb/.env*`, streams `git log -p --all`, and prints only counts:

```bash
python3 - <<'PY'
import os, subprocess
def load(path):
    out = {}
    for line in open(os.path.expanduser(path), encoding="utf-8"):
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            if any(s in k.upper() for s in ("PASS", "SECRET", "KEY", "TOKEN", "PIN")) and len(v) >= 6:
                out[k] = v
    return out
values = {}
for p in ("~/.config/algo-orb/.env", "~/.config/algo-orb/.env.production"):
    values.update(load(p))
log = subprocess.run(["git", "log", "-p", "--all"], capture_output=True, text=True).stdout
hits = {k: log.count(v) for k, v in values.items() if v in log}
print("secret hits:", hits)
raise SystemExit(1 if any(hits.values()) else 0)
PY
```
Expected: `secret hits: {}` and exit 0. The script never prints a value. If `python3` is unavailable, run the same script with `uv run --with python-dotenv python` and replace the parser with `dotenv_values`.

- [ ] **Step 6: Force-push the rewritten history**

Run:
```bash
git push --force origin main
git push --force --tags origin
```
Expected: both pushes succeed. If the remote rejects them, stop and report; do not continue with a remote that still contains the secrets.

- [ ] **Step 7: Drop and recreate the database**

Use a mode-600 defaults file as in Step 3, then run:
```bash
mysql --defaults-extra-file=/tmp/algo-orb-drop.cnf \
  -e "DROP DATABASE IF EXISTS algo_orb; CREATE DATABASE algo_orb CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;"
rm -f /tmp/algo-orb-drop.cnf
```
Expected: `algo_orb` exists and is empty. If the account lacks CREATE/DROP, stop and ask the operator to run these two statements.

- [ ] **Step 8: Remove the old tracked tree**

Keep the new spec and plan, remove everything else that Git tracks:

```bash
git ls-files | grep -v -E '^docs/superpowers/(specs/2026-10-29-clean-foundation-design\.md|plans/2026-10-29-clean-foundation\.md)$' | xargs git rm -q
rm -rf app tests migrations deploy reports .setting .opencode .codex alembic.ini compose.yaml Dockerfile .dockerignore README.md AGENTS.md pyproject.toml uv.lock .python-version .env.example 2>/dev/null || true
find .superpowers/sdd -mindepth 1 -maxdepth 1 ! -name 2026-10-29-clean-foundation -exec rm -rf {} +
```
Expected: only `.git`, the two docs, `.env*` (restored later), the current plan workspace, and ignored caches remain. Do not recreate the deleted MCP configs with literal credentials; if the harness later needs them, they must use environment references or omit the MCP entry, and `git grep` must show no secrets.

- [ ] **Step 9: Restore env files and write `.gitignore`**

```bash
cp "$HOME/.config/algo-orb/.env" .env
cp "$HOME/.config/algo-orb/.env.production" .env.production
chmod 600 .env .env.production
```

Create `.gitignore`:
```gitignore
.venv/
__pycache__/
*.py[cod]
.pytest_cache/
.ruff_cache/
.superpowers/
.setting/
backups/
.worktrees/
.env*
!.env.example
```
Expected: `git status` shows `.env*` ignored.

- [ ] **Step 10: Commit the wipe**

```bash
git add .gitignore docs/superpowers
git commit -m "chore: remove old application and purge leaked secrets"
git log --oneline -3
```
Expected: one commit deleting the old tree; `git log --all --diff-filter=A --name-only | grep -E '(^|/)\.env$|\.env\.production$|\.setting/'` prints nothing.

---

### Task 2: Project scaffold, configuration, and logging

**Files:**
- Create: `pyproject.toml`, `.python-version`, `app/__init__.py`, `app/core/__init__.py`, `app/core/errors.py`, `app/core/config.py`, `app/core/logging.py`, `.env.example`, `README.md`, `tests/__init__.py`, `tests/conftest.py`, `tests/test_config.py`, `tests/test_logging.py`
- Update: `AGENTS.md` (same constraints plus the new structure)
- Delete: none

**Interfaces:**
- Consumes: nothing
- Produces: `Settings`, `get_settings()`, `load_settings()`, `reset_settings_cache()`, `Settings.upstox_api_key`, `Settings.upstox_api_secret`, `Settings.upstox_redirect_uri`, `Settings.is_live`, `configure_logging(level: str)`, and the `AppError` hierarchy

- [ ] **Step 1: Write the failing configuration tests**

`tests/test_config.py`:
```python
import pytest
from app.core.config import Settings, get_settings, load_settings, reset_settings_cache
from app.core.errors import ConfigError

BASE = {
    "app_session_secret": "s" * 32, "app_username": "admin", "app_password": "pw",
    "mysql_host": "127.0.0.1", "mysql_user": "app", "mysql_password": "db",
    "mysql_database": "algo_orb",
    "upstox_sandbox_api_key": "sandbox-key", "upstox_sandbox_api_secret": "sandbox-secret",
    "upstox_sandbox_redirect_uri": "http://localhost:8000/auth/upstox/callback",
    "upstox_live_api_key": "live-key", "upstox_live_api_secret": "live-secret",
    "upstox_live_redirect_uri": "https://example.com/auth/upstox/callback",
    "upstox_account_user_id": "acct",
    "order_max_notional": "10000", "order_max_quantity": 1000,
}

def test_missing_secret_fails_without_echoing_values(monkeypatch):
    for k, v in BASE.items(): monkeypatch.setenv(k.upper(), str(v))
    monkeypatch.delenv("APP_SESSION_SECRET")
    with pytest.raises(ConfigError) as caught:
        load_settings(_env_file=None)
    message = str(caught.value)
    assert "app_session_secret" in message
    assert "sandbox-secret" not in message and "live-secret" not in message

def test_sandbox_selects_sandbox_keys():
    s = Settings(_env_file=None, **BASE)
    assert (s.upstox_api_key, s.upstox_api_secret) == ("sandbox-key", "sandbox-secret")
    assert s.is_live is False

def test_live_selects_live_keys():
    s = Settings(_env_file=None, **BASE, upstox_environment="live")
    assert (s.upstox_api_key, s.upstox_api_secret) == ("live-key", "live-secret")
    assert s.is_live is True

def test_trading_safe_defaults():
    s = Settings(_env_file=None, **BASE)
    assert s.trading_mode == "paper" and s.trading_enabled is False

def test_non_positive_limit_is_rejected():
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        Settings(_env_file=None, **{**BASE, "order_max_quantity": 0})

def test_get_settings_is_cached(monkeypatch):
    for k, v in BASE.items(): monkeypatch.setenv(k.upper(), str(v))
    reset_settings_cache()
    assert get_settings(_env_file=None) is get_settings(_env_file=None)
```
`tests/test_logging.py`:
```python
import logging
from app.core.logging import configure_logging

def test_redacts_secret_fields(caplog):
    configure_logging("INFO")
    logging.getLogger("test").info("callback code=%s token=%s", "SECRET-CODE", "SECRET-TOKEN")
    text = caplog.text
    assert "SECRET-CODE" not in text and "SECRET-TOKEN" not in text
    assert "[REDACTED]" in text
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_config.py tests/test_logging.py -v`
Expected: FAIL with `ModuleNotFoundError: app.core.config`.

- [ ] **Step 3: Create the project files**

`pyproject.toml`:
```toml
[project]
name = "algo-orb"
version = "0.1.0"
description = "Trading foundation for Upstox"
requires-python = ">=3.13"
dependencies = [
  "fastapi", "uvicorn", "jinja2", "pymysql", "upstox-python-sdk",
  "python-dotenv", "pydantic-settings", "alembic>=1.19.0",
  "sqlalchemy>=2.0.52", "pwdlib[argon2]>=0.3.1", "python-multipart>=0.0.32",
]

[dependency-groups]
dev = ["pytest", "httpx", "ruff"]

[tool.pytest.ini_options]
testpaths = ["tests"]
markers = ["mysql: requires a live MySQL test database"]

[tool.ruff]
line-length = 100
target-version = "py313"
```
`.python-version` contains `3.13`.

`app/core/config.py` defines `Settings(BaseSettings)` with the fields from the spec, `SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore", case_sensitive=False)`, `Field(min_length=16)` on `app_session_secret`, positive validators on the limits, and properties:
```python
@property
def upstox_api_key(self) -> str: ...
@property
def upstox_api_secret(self) -> str: ...
@property
def upstox_redirect_uri(self) -> str: ...
@property
def is_live(self) -> bool: return self.trading_mode == "live"
def load_settings(_env_file: str | None = ".env") -> Settings: ...
def get_settings(_env_file: str | None = ".env") -> Settings: ...  # lru_cache
def reset_settings_cache() -> None: ...
```
`load_settings` catches `pydantic.ValidationError`, extracts only the field locations, and raises `ConfigError("Invalid configuration: app_session_secret, mysql_password")` — never the input values. `get_settings` caches `load_settings`; `reset_settings_cache` clears it.

`tests/conftest.py` defines reusable fixtures:
```python
class FrozenClock:
    def __init__(self, now=1_700_000_000.0): self._now = now
    def time(self) -> float: return self._now
    def advance(self, seconds: float) -> None: self._now += seconds

@pytest.fixture
def clock() -> FrozenClock: return FrozenClock()

@pytest.fixture
def settings() -> Settings: return Settings(_env_file=None, **BASE)
```
Keep `BASE` in `tests/conftest.py` (or a shared `tests/factories.py`) so Tasks 2–12 reuse the same values.
`app/core/errors.py`:
```python
class AppError(Exception):
    status_code = 500
    public_message = "Something went wrong."
class ConfigError(AppError): ...
class ValidationError(AppError): status_code = 422
class AuthError(AppError): status_code = 401
class UpstoxOAuthError(AppError): status_code = 502
class BrokerRejected(AppError): status_code = 502
class BrokerUnavailable(AppError): status_code = 503
class BrokerUnknown(AppError): status_code = 502
```
`app/core/logging.py` installs `logging.basicConfig` with a `SecretRedactionFilter` that rewrites `token=`, `code=`, `secret=`, `password=`, `access_token=`, and `Authorization:` values to `[REDACTED]` before formatting. `configure_logging` is idempotent.

Create `.env.example` with placeholder values and all new keys, including `TRADING_MODE=paper`, `TRADING_ENABLED=false`, `ORDER_MAX_NOTIONAL=10000`, `ORDER_MAX_QUANTITY=1000`, `UPSTOX_ENVIRONMENT=sandbox`, `UPSTOX_ACCOUNT_USER_ID=`, and the existing key names. No real secrets.
Create `README.md` with setup/run commands and a "no strategy yet" statement.
Create `AGENTS.md` carrying the existing project constraints and the new structure.

Append missing new keys to the local `.env` and `.env.production` without printing values: `TRADING_MODE=paper`, `TRADING_ENABLED=false`, `ORDER_MAX_NOTIONAL=10000`, `ORDER_MAX_QUANTITY=1000`, `UPSTOX_ENVIRONMENT=sandbox`, and `UPSTOX_ACCOUNT_USER_ID=<copy of ORB_BROKER_USER_ID>`. Do this with a non-echoing script; never overwrite an existing key. This keeps Alembic and `uvicorn app.main:app` runnable before Task 12.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv lock && uv sync && uv run pytest tests/test_config.py tests/test_logging.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add pyproject.toml uv.lock .python-version .env.example README.md AGENTS.md app tests .gitignore
git commit -m "feat: add project scaffold, settings, and logging"
```

---

### Task 3: MySQL connection layer and baseline migration

**Files:**
- Create: `alembic.ini`, `migrations/env.py`, `migrations/script.py.mako`, `migrations/versions/0001_baseline.py`, `app/repositories/__init__.py`, `app/repositories/database.py`, `tests/test_database.py`, `tests/test_migration_sql.py`
- Modify: none

**Interfaces:**
- Consumes: `Settings`
- Produces: `get_connection(settings: Settings | None = None) -> pymysql.Connection`, `transaction(settings: Settings | None = None, connect=None)` context manager, `sqlalchemy_url(settings) -> str`, and the four baseline tables

- [ ] **Step 1: Write the failing tests**

`tests/test_database.py`:
```python
from app.repositories.database import sqlalchemy_url, transaction
from app.core.config import Settings

def test_sqlalchemy_url_encodes_credentials(settings):
    url = sqlalchemy_url(settings)
    assert url.startswith("mysql+pymysql://")
    assert "algo_orb" in url

def test_transaction_rolls_back_and_closes():
    class FakeConn:
        committed = rolled_back = closed = False
        def commit(self): self.committed = True
        def rollback(self): self.rolled_back = True
        def close(self): self.closed = True
    conn = FakeConn()
    with pytest.raises(RuntimeError):
        with transaction(connect=lambda: conn):
            raise RuntimeError("boom")
    assert conn.rolled_back and conn.closed and not conn.committed
```
`tests/test_migration_sql.py`:
```python
import subprocess

def test_offline_migration_creates_four_tables():
    result = subprocess.run(["uv", "run", "alembic", "upgrade", "head", "--sql"], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    for table in ("users", "sessions", "upstox_tokens", "order_audit"):
        assert f"CREATE TABLE {table}" in result.stdout
```
(Use the project's table naming exactly; if Alembic emits backticks, assert `CREATE TABLE \`users\`` instead.)

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_database.py tests/test_migration_sql.py -v`
Expected: FAIL with import errors / missing `alembic.ini`.

- [ ] **Step 3: Implement the database layer**

`app/repositories/database.py`:
```python
def sqlalchemy_url(settings: Settings) -> str:
    return URL.create("mysql+pymysql", username=..., password=..., host=..., port=..., database=...).render_as_string(hide_password=False)

@contextmanager
def transaction(settings: Settings | None = None, connect=None) -> Iterator[pymysql.Connection]:
    conn = (connect or get_connection)(settings)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
```
`get_connection` uses `pymysql.connect(..., cursorclass=DictCursor, autocommit=False, charset="utf8mb4")` and raises `ConfigError` on an invalid config; connector errors propagate.

`alembic.ini` sets `script_location = migrations` and leaves the URL empty. `migrations/env.py` builds the URL from `get_settings()` in both offline and online modes, with `target_metadata = None` and explicit migrations.

`migrations/versions/0001_baseline.py` creates the four tables exactly as specified:
- `users`: BIGINT PK autoincrement, username VARCHAR(64) unique not null, password_hash VARCHAR(255) not null, created_at/updated_at DATETIME(3) not null.
- `sessions`: BIGINT PK, token_hash CHAR(64) unique, user_id BIGINT FK `users.id` `ON DELETE CASCADE`, expires_at DATETIME(3) indexed, created_at.
- `upstox_tokens`: client_id VARCHAR(64), user_id VARCHAR(64), access_token VARCHAR(2048), timestamps, composite PK.
- `order_audit`: columns from the spec, unique `client_order_id`, indexed `status`, JSON columns.
`downgrade()` drops in FK order: `order_audit`, `upstox_tokens`, `sessions`, `users`.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_database.py tests/test_migration_sql.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add alembic.ini migrations app/repositories tests/test_database.py tests/test_migration_sql.py
git commit -m "feat: add database layer and baseline migration"
```

---

### Task 4: Security primitives and login throttle

**Files:**
- Create: `app/core/security.py`, `tests/test_security.py`
- Modify: none

**Interfaces:**
- Consumes: `APP_SESSION_SECRET`
- Produces: `hash_password`, `verify_password`, `new_session_token`, `hash_session_token`, `issue_csrf`, `verify_csrf`, `sign_state(secret, payload, ttl_seconds, now=None)`, `verify_state(secret, token, now=None)`, `LoginThrottle`

- [ ] **Step 1: Write the failing tests**

`tests/test_security.py`:
```python
import time
import pytest
from app.core.security import *
from app.core.errors import AuthError

def test_password_round_trip():
    h = hash_password("correct horse battery staple")
    assert verify_password("correct horse battery staple", h)
    assert not verify_password("wrong", h)

def test_session_token_is_random_and_hashed():
    a, b = new_session_token(), new_session_token()
    assert a != b
    assert hash_session_token(a) == hash_session_token(a)
    assert hash_session_token(a) != a and len(hash_session_token(a)) == 64

def test_csrf_rejects_tampering():
    t = issue_csrf("s" * 32)
    assert verify_csrf("s" * 32, t)
    assert not verify_csrf("s" * 32, t + "x")

def test_state_rejects_expiry(clock):
    now = clock.time()
    token = sign_state("s" * 32, {"n": "x"}, ttl_seconds=10, now=now)
    assert verify_state("s" * 32, token, now=now)["n"] == "x"
    with pytest.raises(AuthError): verify_state("s" * 32, token, now=now + 11)

def test_throttle_blocks_after_limit(clock):
    t = LoginThrottle(max_attempts=2, window_seconds=60, time_fn=clock.time)
    t.record_failure("ip")
    t.record_failure("ip")
    with pytest.raises(AuthError): t.check("ip")
    clock.advance(61)
    t.check("ip")  # window expired
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_security.py -v`
Expected: FAIL with `ModuleNotFoundError: app.core.security`.

- [ ] **Step 3: Implement the security helpers**

Use `pwdlib.PasswordHash.recommended()` for Argon2. Use `secrets.token_urlsafe(32)` and `hashlib.sha256(...).hexdigest()` for sessions. HMAC-SHA256 over base64url(JSON) for CSRF and state; `verify_state` raises `AuthError` on bad signature, malformed payload, or expiry. `LoginThrottle` stores failure timestamps per key in memory and prunes outside the window; `check` raises `AuthError("Too many attempts")`.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_security.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/core/security.py tests/test_security.py
git commit -m "feat: add password, session, CSRF, and throttle primitives"
```

---

### Task 5: User and session repositories plus auth service

**Files:**
- Create: `app/repositories/users.py`, `app/repositories/sessions.py`, `app/services/__init__.py`, `app/services/auth.py`, `tests/fakes.py`, `tests/test_auth_service.py`, `tests/test_user_repository.py`, `tests/test_session_repository.py`
- Modify: none

**Interfaces:**
- Consumes: `hash_password`, `verify_password`, `new_session_token`, `hash_session_token`, `LoginThrottle`
- Produces: `User`, `SessionStore`, `UserStore` protocols; `UserRepository`, `SessionRepository`; `AuthService(users, sessions, session_ttl_seconds, clock, throttle)`, `login(username, password) -> str`, `authenticate(token) -> AuthenticatedUser | None`, `logout(token) -> None`

- [ ] **Step 1: Write the failing service tests**

`tests/fakes.py` provides in-memory `FakeUserStore`, `FakeSessionStore`, and a `FrozenClock`.

`tests/test_auth_service.py`:
```python
def test_login_returns_token_and_stores_only_hash(service):
    token = service.login("admin", "pw")
    assert token not in service.sessions.rows          # only the hash is stored
    assert service.authenticate(token).username == "admin"

def test_wrong_password_and_unknown_user_share_message(service):
    with pytest.raises(AuthError, match="Invalid username or password"):
        service.login("admin", "wrong")
    with pytest.raises(AuthError, match="Invalid username or password"):
        service.login("missing", "pw")

def test_expired_session_is_rejected(service, clock):
    token = service.login("admin", "pw")
    clock.advance(43201)
    assert service.authenticate(token) is None

def test_logout_deletes_session(service):
    token = service.login("admin", "pw")
    service.logout(token)
    assert service.authenticate(token) is None

def test_throttle_blocks_sixth_attempt(service):
    for _ in range(5):
        with pytest.raises(AuthError): service.login("admin", "wrong")
    with pytest.raises(AuthError, match="Too many attempts"):
        service.login("admin", "pw")
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_auth_service.py -v`
Expected: FAIL with import errors.

- [ ] **Step 3: Implement repositories and service**

`User` is a frozen dataclass `(id: int, username: str, password_hash: str)`. `UserStore` protocol: `get_by_username`, `upsert`, `delete_sessions_for_user` is on the session store instead. `SessionStore`: `create(user_id, token_hash, expires_at)`, `get_user_by_token_hash(token_hash)`, `delete(token_hash)`, `delete_for_user(user_id)`, `prune_expired(now)`.

`UserRepository` and `SessionRepository` take `settings` and use `transaction()`. Parameterize SQL with `%s`; never interpolate values. `SessionRepository.get_user_by_token_hash` joins `sessions` to `users`.

`AuthService(users, sessions, clock, throttle)`:
- `login`: normalize username, look up, verify hash, create token, hash it, store with `expires_at = now + ttl`, prune expired rows, reset throttle on success; every failure raises `AuthError("Invalid username or password")`.
- `authenticate`: hash token, look up non-expired session, return `AuthenticatedUser`.
- `logout`: delete by hash.
- `LoginThrottle` lives here or in security; use the security helper.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_auth_service.py tests/test_user_repository.py tests/test_session_repository.py -v`
Expected: PASS (repository tests use the `mysql` marker and skip without a test database).

- [ ] **Step 5: Commit**

```bash
git add app/repositories/users.py app/repositories/sessions.py app/services tests/fakes.py tests/test_auth_service.py tests/test_user_repository.py tests/test_session_repository.py
git commit -m "feat: add auth service with MySQL user and session stores"
```

---

### Task 6: Web shell, dashboard, login/logout routes

**Files:**
- Create: `app/main.py`, `app/routers/__init__.py`, `app/routers/pages.py`, `app/routers/auth.py`, `app/templates/base.html`, `app/templates/login.html`, `app/templates/dashboard.html`, `app/static/app.css`, `app/core/templating.py`, `tests/test_routes.py`
- Modify: `tests/fakes.py` (add `build_test_app`)

**Interfaces:**
- Consumes: `AuthService`, `Settings`, `issue_csrf`, `verify_csrf`
- Produces: `create_app(settings=None, *, users=None, sessions=None, tokens=None, audit=None, broker=None, sdk=None) -> FastAPI`, `GET /health`, `GET /login`, `POST /login`, `POST /logout`, `GET /`

- [ ] **Step 1: Write the failing route tests**

`tests/test_routes.py`:
```python
def test_health_is_public_and_does_not_touch_db(client):
    r = client.get("/health")
    assert r.status_code == 200 and r.json() == {"status": "ok"}

def test_dashboard_redirects_to_login(client):
    r = client.get("/", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/login"

def test_login_sets_httponly_session_cookie(client):
    csrf = client.get("/login").cookies["algo_csrf"]
    r = client.post("/login", data={"username": "admin", "password": "pw", "csrf_token": csrf}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/"
    assert "HttpOnly" in r.headers["set-cookie"]
    assert client.get("/").status_code == 200

def test_login_without_csrf_is_403(client):
    assert client.post("/login", data={"username": "admin", "password": "pw"}).status_code == 403

def test_wrong_password_is_generic(client):
    csrf = client.get("/login").cookies["algo_csrf"]
    r = client.post("/login", data={"username": "admin", "password": "bad", "csrf_token": csrf})
    assert r.status_code == 401 and "Invalid username or password" in r.text

def test_logout_clears_session(client, login):
    login()
    csrf = client.cookies["algo_csrf"]
    r = client.post("/logout", data={"csrf_token": csrf}, follow_redirects=False)
    assert r.status_code == 303 and client.get("/", follow_redirects=False).status_code == 303

def test_database_failure_returns_503(failing_client):
    failing_client.cookies.set("algo_session", "bogus")
    r = failing_client.get("/")
    assert r.status_code == 503 and "unavailable" in r.text.lower()
```
`tests/fakes.py` provides `build_test_app(...)`, and `tests/conftest.py` adds `client`, `failing_client`, and `login` fixtures on top of it.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_routes.py -v`
Expected: FAIL with import errors.

- [ ] **Step 3: Implement the app and routes**

`create_app` builds a FastAPI instance, mounts `/static`, configures Jinja2, and stores dependencies on `app.state`. When dependencies are not injected, construct them from `get_settings()`. Register an exception handler that maps `AppError` to a sanitized template/JSON response; database errors become 503.

`pages.py`:
- `/health` returns `{"status": "ok"}` with no dependencies.
- `/login` issues a CSRF token, sets an `algo_csrf` cookie (`HttpOnly=True`, `SameSite=Lax`, `Secure` per settings), and renders `login.html` with the token in a hidden field.
- `/` requires a session; unauthenticated redirects to `/login`; renders `dashboard.html` with username and Upstox status (placeholder until Task 7).
`auth.py`:
- `POST /login` verifies CSRF (cookie equals form token and signature valid), calls `AuthService.login`, sets `algo_session` HttpOnly cookie, redirects 303 `/`.
- `POST /logout` verifies CSRF, calls `logout`, deletes the cookie, redirects 303 `/login`.
- A `pymysql.MySQLError` escaping a route becomes a sanitized 503 template; `/health` never touches the database. Register this handler once in `create_app`.
Templates use a shared base with no external assets; CSS is minimal and local.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_routes.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/main.py app/routers app/templates app/static app/core/templating.py tests/fakes.py tests/test_routes.py
git commit -m "feat: add login, logout, dashboard, and health routes"
```

---

### Task 7: Upstox OAuth integration

**Files:**
- Create: `app/services/upstox_sdk.py`, `app/services/upstox_auth.py`, `app/repositories/upstox_tokens.py`, `app/routers/upstox.py`, `tests/test_upstox_auth.py`, `tests/test_upstox_routes.py`
- Modify: `app/main.py`, `app/templates/dashboard.html`, `tests/fakes.py`

**Interfaces:**
- Consumes: `Settings`, `sign_state`, `verify_state`, `TokenStore`
- Produces: `TokenStore` protocol (`get`, `upsert`, `delete`), `UpstoxSdk` protocol (`authorization_url(state)`, `exchange_code(code)`, `profile(access_token)`), `UpstoxAuthService.login_url() -> tuple[str, str]`, `complete(code, state) -> None`, `status() -> str`

- [ ] **Step 1: Write the failing service and route tests**

`tests/test_upstox_auth.py`:
```python
def test_login_url_contains_signed_state(service):
    url, state = service.login_url()
    assert "state=" in url and verify_state(service.secret, state)["n"]

def test_complete_stores_token_for_configured_account(service, fake_sdk, settings):
    _, state = service.login_url()
    service.complete("code-1", state)
    assert service.tokens.get(settings.upstox_api_key, settings.upstox_account_user_id) == "token-1"

def test_bad_state_does_not_exchange(service, fake_sdk):
    with pytest.raises(UpstoxOAuthError):
        service.complete("code-1", "tampered")
    assert fake_sdk.exchanges == []

def test_status_reports_connected_disconnected_unavailable(service, fake_sdk):
    assert service.status() == "disconnected"
    service.complete("code", service.login_url()[1])
    assert service.status() == "connected"
    fake_sdk.fail = True
    assert service.status() == "unavailable"
```
`tests/test_upstox_routes.py`:
```python
def test_callback_sets_no_token_in_url_or_body(client, login):
    login()
    r = client.get("/auth/upstox/login", follow_redirects=False)
    state_cookie = client.cookies["algo_upstox_state"]
    r = client.get(f"/auth/upstox/callback?code=abc&state={state_cookie}", follow_redirects=False)
    assert r.status_code == 303
    assert "abc" not in r.headers["location"] and "token" not in r.text.lower()

def test_callback_rejects_state_not_matching_cookie(client, login):
    login()
    client.cookies.set("algo_upstox_state", "cookie-state")
    r = client.get("/auth/upstox/callback?code=abc&state=other", follow_redirects=False)
    assert r.status_code == 400
```
(Use a fake SDK injected through `create_app`; no network.)

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_upstox_auth.py tests/test_upstox_routes.py -v`
Expected: FAIL with import errors.

- [ ] **Step 3: Implement the SDK wrapper and service**

`UpstoxSdk` protocol isolates the official SDK. `OfficialUpstoxSdk` uses `upstox_client.Configuration`, `ApiClient`, `LoginApi`, and `UserApi`; read the installed SDK's method signatures with `uv run python -c "import upstox_client; help(upstox_client.LoginApi)"` before writing it. Authorization URL, code exchange, and profile call all go through the SDK; no hand-rolled HTTP calls.

`UpstoxAuthService`:
- `login_url()` signs `{"n": nonce}` with a 600-second TTL and returns the SDK URL plus the state.
- `complete(code, state)` verifies state, exchanges once, fetches the profile, and upserts `(client_id, upstox_account_user_id)` with the access token. Missing/invalid state raises `UpstoxOAuthError`; exchange/profile failures raise `UpstoxOAuthError`.
- `status()` returns `connected` when the profile call succeeds, `disconnected` when no token exists, and `unavailable` on transport/SDK errors.

`app/routers/upstox.py`:
- `GET /auth/upstox/login` requires a session, sets `algo_upstox_state` cookie (HttpOnly, `SameSite=Lax`, 600s, `Secure` per settings), redirects to the URL.
- `GET /auth/upstox/callback` requires the cookie, rejects a query `state` that is not equal to the cookie value, calls `complete`, clears the state cookie, redirects to `/?upstox=connected` or `/?upstox=error`; never includes code or token.
- `GET /auth/upstox/status` requires a session and returns `{"status": ...}`.
Dashboard renders the status from the service (server-side), showing Connected / Disconnected / Status unavailable.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_upstox_auth.py tests/test_upstox_routes.py tests/test_routes.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/services/upstox_sdk.py app/services/upstox_auth.py app/repositories/upstox_tokens.py app/routers/upstox.py app/main.py app/templates/dashboard.html tests
git commit -m "feat: add Upstox OAuth flow and token storage"
```

---

### Task 8: Broker port, paper broker, risk limits, and readiness

**Files:**
- Create: `app/brokers/__init__.py`, `app/brokers/base.py`, `app/brokers/paper.py`, `app/services/risk.py`, `app/services/readiness.py`, `tests/test_paper_broker.py`, `tests/test_risk.py`, `tests/test_readiness.py`
- Modify: none

**Interfaces:**
- Consumes: `TokenStore`, `Settings`, `AppError`
- Produces: `OrderRequest`, `OrderResult`, `OrderSnapshot`, `OrderBroker`, `PaperBroker`, `RiskLimits`, `validate`, `ReadinessService.check(mode)`

- [ ] **Step 1: Write the failing tests**

`tests/test_risk.py`:
```python
def test_accepts_limit_order_within_limits(limits):
    validate(limit_order(quantity=10, price="100"), limits)  # no raise

def test_rejects_over_notional(limits):
    with pytest.raises(ValidationError): validate(limit_order(quantity=11, price="100"), limits)

def test_rejects_over_quantity(limits):
    with pytest.raises(ValidationError): validate(limit_order(quantity=1001, price="1"), limits)

def test_rejects_zero_quantity_and_missing_price(limits):
    with pytest.raises(ValidationError): validate(limit_order(quantity=0, price="100"), limits)
    with pytest.raises(ValidationError): validate(limit_order(quantity=1, price=None), limits)

def test_rejects_market_orders(limits):
    with pytest.raises(ValidationError, match="LIMIT"):
        validate(limit_order(quantity=1, price="100", order_type="MARKET"), limits)
```
`tests/test_paper_broker.py`:
```python
def test_place_get_and_cancel_are_deterministic(broker):
    result = broker.place_order(limit_order(client_order_id="abc", quantity=1, price="10"))
    assert result.status == "ACCEPTED" and result.broker_order_id == "paper-abc"
    assert broker.get_order("paper-abc").status == "ACCEPTED"
    assert broker.cancel_order("paper-abc").status == "CANCELLED"
    assert broker.get_order("paper-abc").status == "CANCELLED"
```
`tests/test_readiness.py`:
```python
def test_live_disabled_fails(settings):
    with pytest.raises(BrokerUnavailable): ReadinessService(settings, tokens).check("live")

def test_live_enabled_requires_token(settings, tokens):
    settings = settings.model_copy(update={"trading_enabled": True})
    with pytest.raises(BrokerUnavailable): ReadinessService(settings, tokens).check("live")
    tokens.upsert(settings.upstox_api_key, settings.upstox_account_user_id, "tok")
    ReadinessService(settings, tokens).check("live")

def test_paper_never_requires_a_token(settings, tokens):
    ReadinessService(settings, tokens).check("paper")
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_risk.py tests/test_paper_broker.py tests/test_readiness.py -v`
Expected: FAIL with import errors.

- [ ] **Step 3: Implement the broker and risk layer**

`app/brokers/base.py` defines frozen dataclasses with `Decimal` prices and the `OrderBroker` protocol from the spec. `PaperBroker` stores snapshots in a dict, generates `paper-<client_order_id>`, rejects duplicate client order IDs with `BrokerRejected`, and supports get/cancel.

`risk.validate` enforces:
- `client_order_id` non-empty and at most 64 characters
- `instrument` non-empty
- `quantity > 0` and `quantity <= max_quantity`
- `side in {"BUY", "SELL"}` and `product in {"I", "D"}`
- `order_type == "LIMIT"` (MARKET rejected until a reference-price rule exists)
- `price` present and `> 0`
- `quantity * price <= max_notional`

`ReadinessService.check(mode)`:
- `paper`: always ready.
- `live`: `settings.trading_enabled` must be true and a token must exist for `(client_id, upstox_account_user_id)`; otherwise raise `BrokerUnavailable` with a sanitized reason.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_risk.py tests/test_paper_broker.py tests/test_readiness.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/brokers app/services/risk.py app/services/readiness.py tests/test_risk.py tests/test_paper_broker.py tests/test_readiness.py
git commit -m "feat: add broker port, paper broker, risk limits, and readiness"
```

---

### Task 9: Order audit repository and gateway

**Files:**
- Create: `app/repositories/audit.py`, `app/services/order_gateway.py`, `tests/test_order_gateway.py`, `tests/test_audit_repository.py`
- Modify: none

**Interfaces:**
- Consumes: `OrderBroker`, `validate`, `RiskLimits`, `ReadinessService`
- Produces: `AuditStore` protocol (`create_intent`, `mark_accepted`, `mark_rejected`, `mark_unknown`, `get`), `AuditRepository`, `OrderGateway(audit, broker, limits, readiness, mode)`, `submit(request, strategy_ref=None) -> OrderResult`

- [ ] **Step 1: Write the failing gateway tests**

`tests/test_order_gateway.py`:
```python
def test_valid_order_audits_intent_then_accepted(gateway, audit, broker):
    result = gateway.submit(limit_order(client_order_id="abc", quantity=1, price="10"))
    assert result.status == "ACCEPTED"
    assert audit.statuses("abc") == ["INTENT", "ACCEPTED"]
    assert broker.calls == ["abc"]

def test_validation_failure_never_audits_or_calls_broker(gateway, audit, broker):
    with pytest.raises(ValidationError):
        gateway.submit(limit_order(client_order_id="bad", quantity=0, price="10"))
    assert audit.rows == {} and broker.calls == []

def test_not_ready_marks_rejected_without_broker_call(gateway, audit, broker):
    gateway.readiness.fail = True
    with pytest.raises(BrokerUnavailable):
        gateway.submit(limit_order(client_order_id="abc", quantity=1, price="10"))
    assert audit.statuses("abc") == ["INTENT", "REJECTED"] and broker.calls == []

def test_ambiguous_broker_response_marks_unknown_and_does_not_retry(gateway, audit, broker):
    broker.raise_unknown = True
    with pytest.raises(BrokerUnknown):
        gateway.submit(limit_order(client_order_id="abc", quantity=1, price="10"))
    assert audit.statuses("abc") == ["INTENT", "UNKNOWN"]
    assert broker.calls == ["abc"]

def test_audit_failure_prevents_broker_call(gateway, audit, broker):
    audit.fail_create = True
    with pytest.raises(Exception):
        gateway.submit(limit_order(client_order_id="abc", quantity=1, price="10"))
    assert broker.calls == []
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_order_gateway.py -v`
Expected: FAIL with import errors.

- [ ] **Step 3: Implement the audit store and gateway**

`AuditRepository` uses `transaction()` and parameterized SQL. `create_intent` inserts `(client_order_id, strategy_ref, request_json, status='INTENT')`; update methods write `broker_order_id`, sanitized response JSON, `error_code`, `updated_at`, and guard on the current status where appropriate. `get` returns a frozen `AuditRecord`.

`OrderGateway.submit` follows the spec exactly:

```python
validate(request, self.limits)
self.audit.create_intent(request, strategy_ref)
try:
    self.readiness.check(self.mode)
except AppError as exc:
    self.audit.mark_rejected(request.client_order_id, code=type(exc).__name__)
    raise
try:
    result = self.broker.place_order(request)   # exactly once
except (BrokerUnavailable, BrokerUnknown) as exc:
    self.audit.mark_unknown(request.client_order_id, code=type(exc).__name__, message=str(exc))
    raise
except BrokerRejected as exc:
    self.audit.mark_rejected(request.client_order_id, code="BROKER_REJECTED", message=str(exc))
    raise
if result.status == "ACCEPTED":
    self.audit.mark_accepted(request.client_order_id, result.broker_order_id, result.message)
elif result.status == "REJECTED":
    self.audit.mark_rejected(request.client_order_id, code="BROKER_REJECTED", message=result.message)
    raise BrokerRejected(result.message or "Order rejected")
return result
```
Sanitize `str(exc)`/`result.message` before storing. Never retry.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_order_gateway.py tests/test_audit_repository.py -v`
Expected: PASS (repository tests skip without `mysql`).

- [ ] **Step 5: Commit**

```bash
git add app/repositories/audit.py app/services/order_gateway.py tests/test_order_gateway.py tests/test_audit_repository.py
git commit -m "feat: add order audit and safe order gateway"
```

---

### Task 10: Admin sync CLI

**Files:**
- Create: `app/cli/__init__.py`, `app/cli/sync_admin.py`, `tests/test_sync_admin.py`
- Modify: `tests/fakes.py`

**Interfaces:**
- Consumes: `Settings`, `UserStore`, `SessionStore`, `hash_password`, `verify_password`
- Produces: `run(settings, users, sessions) -> int`, `main() -> int`

- [ ] **Step 1: Write the failing tests**

`tests/test_sync_admin.py`:
```python
def test_creates_admin_and_hashes_password(settings, users, sessions):
    assert run(settings, users, sessions) == 0
    user = users.get_by_username("admin")
    assert user and verify_password("pw", user.password_hash)

def test_is_idempotent_when_password_matches(settings, users, sessions):
    run(settings, users, sessions)
    before = users.get_by_username("admin").password_hash
    assert run(settings, users, sessions) == 0
    assert users.get_by_username("admin").password_hash == before

def test_password_change_revokes_sessions(settings, users, sessions):
    run(settings, users, sessions)
    user = users.get_by_username("admin")
    sessions.create(user.id, "a" * 64, expires_at=9_999_999_999)
    changed = settings.model_copy(update={"app_password": "new-pw"})
    assert run(changed, users, sessions) == 0
    assert sessions.get_user_by_token_hash("a" * 64) is None
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_sync_admin.py -v`
Expected: FAIL with import errors.

- [ ] **Step 3: Implement `run` and `main`**

`run` is dependency-injected for tests. It looks up the user; when absent, upserts with a fresh Argon2 hash. When present, verify the configured password against the stored hash: if it matches, do nothing; otherwise replace the hash and delete that user's sessions. Never print the password or hash; print only `admin user ready` and return 0. `main()` wires `get_settings()`, real repositories, and `run`, returning 0 or 1 on `AppError`.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_sync_admin.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/cli tests/fakes.py tests/test_sync_admin.py
git commit -m "feat: add idempotent admin sync CLI"
```

---

### Task 11: Deployment assets and documentation

**Files:**
- Create: `Dockerfile`, `.dockerignore`, `compose.yaml`, `deploy/nginx/algo-orb.conf`, `deploy/README.md`, `deploy/production.env.example`
- Modify: `README.md`, `AGENTS.md`

**Interfaces:**
- Consumes: the app entrypoint `app.main:app`, `alembic upgrade head`, `app.cli.sync_admin`
- Produces: buildable image, valid Compose config, deployment instructions

- [ ] **Step 1: Write the deployment files**

`Dockerfile`: multi-stage `ghcr.io/astral-sh/uv` + `python:3.13-slim-bookworm`, `uv sync --frozen --no-dev`, copy `app`, `migrations`, `alembic.ini`, non-root UID 10001, `CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1", "--no-access-log", "--proxy-headers", "--forwarded-allow-ips", "172.30.21.1"]`.

`compose.yaml`: only the `web` service, `APP_ENV_FILE` default `.env.production`, `127.0.0.1:3000:8000`, `read_only`, `cap_drop: ALL`, `no-new-privileges`, tmpfs `/tmp`, healthcheck on `/health`, log rotation, `MYSQL_HOST=host.docker.internal`, `extra_hosts`. No worker or token-scheduler.

`deploy/nginx/algo-orb.conf`: HTTPS server proxying to `127.0.0.1:3000`, access logs off.
`deploy/production.env.example`: new keys only, placeholders.
`deploy/README.md`: setup, migration, `sync_admin`, start, HTTPS, secret handling, and the explicit note that no trading worker exists yet.
`README.md` and `AGENTS.md`: reflect the new foundation, no strategy, and the safety rules.

- [ ] **Step 2: Validate the deployment files**

Run:
```bash
docker compose config >/dev/null && echo "compose ok"
docker build -t algo-orb:foundation .
```
Expected: `compose ok`; image builds. If Docker is unavailable, record that and verify `docker compose config` syntax manually; do not claim it passed.

- [ ] **Step 3: Commit**

```bash
git add Dockerfile .dockerignore compose.yaml deploy README.md AGENTS.md
git commit -m "chore: add production deployment assets and docs"
```

---

### Task 12: End-to-end verification and handoff

**Files:**
- Modify: `~/.config/algo-orb/.env*` / local `.env*` only (never tracked)
- Create: `docs/operations/2026-10-29-foundation-verification.md`

**Interfaces:**
- Consumes: everything above
- Produces: a verified foundation and a short verification record

- [ ] **Step 1: Restore and complete local env files**

Add the new keys to local `.env` in place without printing values:
`UPSTOX_ENVIRONMENT`, `UPSTOX_SANDBOX_*`, `UPSTOX_LIVE_*`, `UPSTOX_ACCOUNT_USER_ID` (copy from the old `ORB_BROKER_USER_ID` if present), `TRADING_MODE=paper`, `TRADING_ENABLED=false`, `ORDER_MAX_NOTIONAL`, `ORDER_MAX_QUANTITY`. Confirm `git status --ignored | grep -E '^!! \.env'` and that no secret is tracked.

- [ ] **Step 2: Run the quality gates**

```bash
uv sync
uv run ruff check .
uv run pytest -v
```
Expected: ruff clean; all tests pass; MySQL tests skip if no test database.

- [ ] **Step 3: Migrate and seed a fresh database**

```bash
uv run alembic upgrade head
uv run alembic current
uv run python -m app.cli.sync_admin
```
Expected: `0001` is current; four application tables plus `alembic_version`; `admin user ready`.

- [ ] **Step 4: Smoke-test the running app**

```bash
uv run uvicorn app.main:app --host 127.0.0.1 --port 8000 --no-access-log &
curl -fsS http://127.0.0.1:8000/health
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8000/login
```
Expected: `{"status":"ok"}` and `200`. Stop the server afterward.

- [ ] **Step 5: Complete the manual OAuth and safety checks**

- Log in with the rotated admin password.
- Connect Upstox with the rotated keys; confirm Connected and that no token appears in the URL, page source, or logs.
- Exercise `OrderGateway` against `PaperBroker` in tests only (already covered); confirm no route or CLI can submit a live order.
- Confirm `TRADING_ENABLED=false` blocks live readiness in the test suite.

- [ ] **Step 6: Verify history is clean**

```bash
git log --all --diff-filter=A --name-only | grep -E '(^|/)\.env$|\.env\.production$|\.setting/|\.opencode/opencode\.json|\.codex/config\.toml' || echo "history clean"
git status --short
```
Expected: `history clean`; working tree clean except ignored files. Also rerun the Task 1 Step 5 secret-value scan against `~/.config/algo-orb` copies; the result must be zero hits.

- [ ] **Step 7: Write the verification record and commit**

`docs/operations/2026-10-29-foundation-verification.md` records the commands run, their results, any skipped checks (Docker unavailable, MySQL test DB absent), and the remaining operator actions: rotate credentials and confirm broker-side GTTs are settled.

```bash
git add docs/operations/2026-10-29-foundation-verification.md
git commit -m "docs: record foundation verification"
```
