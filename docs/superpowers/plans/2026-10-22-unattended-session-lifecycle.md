# Unattended session lifecycle implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans for native execution or superpowers:subagent-driven-development if the user selects delegation. Track steps below.

**Goal:** Renew saved broker credentials before each weekday session, supervise one worker, and stop cleanly after the session without concealing authentication failures.

**Architecture:** A supervisor owns scheduling and a child worker owns trading. Pure lifecycle decisions are separate from process management and MySQL session persistence. The worker keeps existing entry, exit, reconciliation and risk rules.

**Tech Stack:** Python, official Upstox SDK, MySQL/PyMySQL, Alembic, Jinja2, Docker Compose; existing uv environment.

**Spec:** `docs/superpowers/specs/2026-10-22-unattended-session-lifecycle-design.md`.

## Global constraints

- Covers paper and live modes; does not enable live trading or change configured mode or limits.
- No live broker calls or orders during development or tests. Never print secrets or broker exception bodies.
- Credentials rotate only with account locks owned and no unresolved exposure, except the explicit exposure override.
- Initial connection remains an explicit `--connect` action.
- Defaults: preflight `08:45`, open `09:15`, close `15:30`, exposure override `false`; all times IST.
- Session phases: `preflight`, `token_valid`, `token_renewed`, `started`, `closed`, `skipped`, `reconciling`, `failed`.
- Token states: `valid`, `renewed`, `invalid`, `missing`; nullable before verification.
- Database schema changes use Alembic revision `0009` after `0008`.

## Review focus

1. Restart after open, across midnight, or over weekends must neither duplicate entries nor strand existing exposure (tasks 1, 4).
2. A 401 during entry, exit, recovery or GTT management must preserve uncertain intent and reach worker recovery (tasks 2, 3).
3. Lock handoff permits another process to win; a losing worker must stop before any broker mutation (task 3).
4. SIGTERM during preflight, retry wait or child startup must leave no orphan and no false closed status (task 4).
5. Database failures and mode changes must not turn unknown exposure into permission to renew or hide earlier live exposure (tasks 1, 3–5).

## Design resolutions

- `09:15` is the scheduled initial start. Failed preflight cannot start a fresh session after that deadline. Restarts may resume an already-started session before close; unresolved exposure may be reconciled at any time, including weekends. Otherwise a restart after the deadline records a missed session and waits.
- Ordinary nonzero exits retry every 300 seconds while the window is open, and afterwards only for unresolved exposure. An authentication exit with exposure and override disabled is distinct and suppresses all automatic child retries for that session date, including supervisor restarts. This resolves the conflicting retry/default-fail-closed clauses in the spec.
- Renewal checks unresolved runs in both modes under the two account locks. A free worker lock alone does not prove an account has no exposure. Unknown database state prohibits rotation.
- Missing credentials skip without loading PIN/TOTP settings. Valid saved credentials work without configured PIN/TOTP. Only confirmed HTTP 401 authorizes token rotation; network failures retry verification without rotating.
- Rebuilding a paper adapter must not discard in-memory simulated orders while persisted exposure remains. Default flat recovery rebuilds safely; exposure override must preserve paper execution state and update its quote provider, or explicitly refuse recovery if safe preservation is impossible.
- Keep the spec's single-account session key `(session_date, mode)`. Persist override use explicitly as `renewed_with_exposure` (boolean) in addition to its required columns; fixed reason codes carry authentication retry suppression. Account changes are outside scope.
- Docker restart destroys the old child and starts a new supervisor; it does not restore the old child process. Startup reconciliation implements recovery.

## Task 1: Lifecycle settings, pure decisions and persistence

**Files:** create `app/services/session_lifecycle.py`, `app/repositories/strategy_sessions.py`, `migrations/versions/0009_strategy_sessions.py`, `tests/test_session_lifecycle.py`, `tests/test_mysql_strategy_sessions.py`; modify `tests/test_migrations.py`.

**Interfaces:** `SessionSettings(start: time, open: time, close: time, renew_with_exposure: bool)` and `load_session_settings() -> SessionSettings`; `next_preflight(now: datetime, settings: SessionSettings) -> datetime`; `session_finished(now: datetime, settings: SessionSettings, latest: StrategyRun | None, unresolved: list[StrategyRun]) -> bool`. Repository exposes `get(session_date, mode)`, `latest(mode)`, and `upsert(session_date, mode, **fields)` returning a typed `StrategySession` with the migration's columns. Shared `has_unresolved_exposure(settings: TradingSettings) -> bool` queries both modes and propagates database errors.

- [ ] Write parameterized tests for weekday/weekend windows, exact boundaries, aware times, invalid time ordering/flags, terminal versus unresolved runs, prior-date exposure and database errors. Write MySQL tests asserting one upsert row per date/mode, preservation of omitted fields, enum validation and override persistence.
- [ ] Run `.venv/bin/python -m pytest tests/test_session_lifecycle.py tests/test_mysql_strategy_sessions.py tests/test_migrations.py -q`; confirm missing-feature failures. Integration tests use the existing opt-in isolated MySQL convention.
- [ ] Implement pure decisions, strict environment parsing, typed session rows and idempotent upserts. Use fixed reason vocabulary; reject unsupported update fields. No schema work in request paths.
- [ ] Repeat tests; inspect upgrade/downgrade SQL and run the isolated MySQL test if the configured test database is available.

## Task 2: Preserve authentication errors through all execution paths

**Files:** modify `app/services/trading_market.py`, `app/strategies/engine.py`, `app/strategies/gtt.py`, `tests/test_trading_market.py`, `tests/test_strategy_engine.py`, `tests/test_gtt_engine.py`.

**Interfaces:** `BrokerUnauthenticated(RuntimeError)` separate from `TradingUnavailable`; `sdk_data()` raises it on SDK HTTP 401. All engine read/recovery paths propagate it; mutation paths persist reconciliation before propagation.

- [ ] Add SDK fake tests asserting 401 classification, sanitized messages and unchanged timeout/5xx behavior. Add engine tests for 401 before signal, after signal, during pending entry, stop/exit mutation, GTT placement and GTT recovery. Assert no duplicate intent or order and persisted reconciliation where outcomes are uncertain.
- [ ] Run `.venv/bin/python -m pytest tests/test_trading_market.py tests/test_strategy_engine.py tests/test_gtt_engine.py -q`; confirm new tests fail for swallowed or misclassified authentication errors.
- [ ] Classify the exception at the SDK boundary and add explicit authentication handling before broad catches throughout both execution backends. Retain existing generic-error behavior.
- [ ] Repeat these tests and broker adapter tests; all must pass.

## Task 3: Worker session end and safe credential handoff

**Files:** modify `app/cli/run_strategy.py`, `app/services/upstox_totp.py`, `tests/test_strategy_worker.py`, `tests/test_upstox_totp.py`; consume task 1 lifecycle/persistence and task 2 exception.

**Interfaces:** retain `run_worker(settings, stop, *, clock=None) -> int`; add a documented distinct authentication-with-exposure exit code. Extend `retrieve_token()` with a default-false exposure override and exposure checks inside its existing two-lock scope. Existing CLI callers remain compatible.

- [ ] Add failing tests for flat clean exit at 15:30, pending orders/previous-date exposure preventing exit, and session-end checks after unsuccessful ticks. Add startup/recover/tick 401 cases, lock release before renewal, reacquisition failure, credential race, renewal failure and SDK cleanup.
- [ ] Add tests proving exposure blocks renewal in either mode, override use records a warning/session flag, unknown exposure blocks rotation, and paper state survives allowed client refresh. Assert defaults never invoke renewal with exposure.
- [ ] Run `.venv/bin/python -m pytest tests/test_strategy_worker.py tests/test_upstox_totp.py -q` and verify new failures.
- [ ] Implement bounded authentication recovery with token reload, account verification and lock revalidation before resuming. Mark unresolved runs for reconciliation before the default authentication exit. Evaluate clean session end from fresh repository reads; database errors keep the worker from declaring itself flat.
- [ ] Repeat worker/token tests plus engine tests; verify every resource closes on each return path.

## Task 4: Supervisor orchestration and deployment

**Files:** create `app/cli/session_supervisor.py`, `tests/test_session_supervisor.py`; modify `compose.yaml`, `.env.example` (if present), `README.md`, `deploy/README.md`; create `docs/operations/2026-10-22-session-lifecycle.md`.

**Interfaces:** `run_supervisor(stop: Event, *, clock=None, sleep=None, spawn=None) -> int`; `main() -> int`. Spawn `[sys.executable, '-m', 'app.cli.run_strategy']` with inherited environment. Use injected clock/waits/process fakes; production waits remain interruptible. Token verification uses one read-only SDK profile call and checks pinned identity; SDK client is closed.

- [ ] Add failing fake-driven tests for valid token reuse without TOTP, missing token skip, confirmed-401 renewal, timeout verification retry without rotation, 60-second preflight retry/deadline, invalid trading config persisted without child launch, and no exposure rotation across modes.
- [ ] Add restart/window tests matching the design resolutions, a single child invariant, persisted authentication retry suppression, 300-second ordinary failure retry, after-close reconciliation, nonzero exit exposure reread, clean close, and no next-session entries while older runs remain unresolved.
- [ ] Add signal tests for shutdown during sleep/start/running child: forward the signal, reap child and record actual exposure/exit. Failure to read exposure records failure and never claims closed.
- [ ] Run `.venv/bin/python -m pytest tests/test_session_supervisor.py -q`; verify expected missing-feature failures.
- [ ] Implement orchestration and fixed application reasons. Replace worker/token-scheduler Compose services with supervisor and a sufficient stop grace period. Document `--remove-orphans` rollout to retire old services, migration before startup, manual exposure reconciliation, and override risk. Do not deploy or rotate real credentials as part of implementation verification.
- [ ] Repeat supervisor tests; validate rendered Compose without exposing interpolated environment or secrets.

## Task 5: Dashboard status and final verification

**Files:** modify `app/services/strategy_status.py`, `app/templates/index.html`, `app/templates/strategy_history.html`, `tests/test_strategy_status.py`, `tests/test_strategy_history.py`; follow existing banner markup. Read UI skill before template edits.

**Interfaces:** append `session: StrategySession | None = None` and `session_message: str = ''` to `StrategyStatus`, preserving existing positional callers. Retrieve the session matching the projected mode and display sanitized fixed copy on both pages.

- [ ] Add failing projection/render tests for missing token, renewal failure, normal no-exposure close, reconciliation, unexpected exit code, no session row, repository failure, and earlier live exposure despite selected paper mode. Assert untrusted reasons are not rendered raw.
- [ ] Run `.venv/bin/python -m pytest tests/test_strategy_status.py tests/test_strategy_history.py -q`; verify new failures.
- [ ] Implement session projection and accessible banners beside existing heartbeat information, including no-run skipped sessions. Preserve exposure warnings and mode selection behavior.
- [ ] Run the full `.venv/bin/python -m pytest` suite and repository lint/type checks documented for this project. Report skipped integration checks and any baseline failures explicitly. Inspect `git diff --check`, migration chain and final diff for secrets and accidental trading-rule changes.
- [ ] Review the whole change against the spec and these design resolutions before reporting completion. Leave deployment and any live credential/order operations out of automated tests.

## Execution handoff

Recommended method: native execution, since scheduling, persistence and worker error handling share tightly coupled interfaces. User review of this plan is pending; implementation has not started. No commits or deployment actions have been made.
