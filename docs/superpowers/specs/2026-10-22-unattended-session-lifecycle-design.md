# Unattended session lifecycle for the RELIANCE ORB worker

Status: approved in conversation on 2026-10-22. Covers paper and live modes.

## Intent

Make the daily broker session fully unattended: renew credentials automatically
before the opening range is built, start the strategy worker for the session,
and stop it afterwards, without an operator present. Today the paper and live
workers are left running across the day boundary, which blocks the token
scheduler's renewal and silently costs whole sessions.

The incident that motivates this is concrete. On 22 October 2026 the pinned
Upstox token was last issued on 21 October at 08:30 IST and was no longer
valid. The worker kept ticking until 15:00 IST, every iteration failing on the
option-contract snapshot because an expired token and a network fault were
indistinguishable to it. The run stayed in `waiting_range` with no opening
range, recorded no signal, and was closed as `skipped / Entry cutoff passed`.
The dashboard showed a current worker heartbeat for the whole session, which
is exactly the state an operator needs to be able to trust.

Two changes fix this and remove the trap that caused it:

1. The token scheduler currently refuses to renew while any strategy worker
   holds the account lock, and attempts at most once per IST date. A worker
   that never stops therefore suppresses renewal for the following day.
2. The worker loop has no notion of a session ending. After the 15:00 entry
   cutoff it keeps heartbeating indefinitely, holding the lock all night.

## Operating model

A new long-running process, the session supervisor, owns the daily cycle. It
never trades, never holds the account lock while a worker is running, and
decides only when to renew, when to start the worker and when the session is
over.

The cycle, all times IST:

| Time | Action |
| --- | --- |
| 08:45, Monday to Friday | Read the execution mode from `trading_config`. Read the saved token for the pinned account. Verify it with one authenticated read-only call. |
| | If the token is valid, reuse it. If it is not, renew it through the existing PIN/TOTP flow. The advisory lock is free at this point because no worker is running, so the existing lock-guarded renewal works unchanged. |
| | If renewal keeps failing, retry about every 60 seconds until 09:15 IST, record the session as skipped, and do not start the worker. |
| 09:15 | Spawn `python -m app.cli.run_strategy` as a child process inside the same container. The child takes the advisory lock; a second child would fail to acquire it and exit. |
| 09:15 to 15:25 | The worker ticks. The engine runs its existing 15:25 IST time exit. |
| 15:30 or later | The worker exits cleanly, but only when nothing is exposed. The supervisor records the outcome and sleeps. |
| Next weekday 08:45 | The cycle repeats. |

Two invariants hold throughout. Only one worker child exists at a time, and
credentials are only rotated while nothing is exposed. Exchange holidays need
no special handling: the worker starts, observes that the session is not
normal, idles, and exits at 15:30 without entering.

## Supervisor component

`app/cli/session_supervisor.py` is the new entry point. It is a thin process
whose decisions are pure functions in `app/services/session_lifecycle.py`, so
the timing and decision logic can be tested without a clock, a broker or a
database.

The supervisor performs these steps in order for each session:

1. Compute the next pre-open window and sleep until it.
2. Load the execution mode, the pinned account and the trading settings through
   the existing `load_trading_settings()`, so the supervisor and the worker
   read one source of truth. If that call raises, for example because live mode
   is selected without explicit enablement or configured limits, the session is
   recorded as skipped with that reason and no worker is started.
3. Verify the saved token for the pinned account with a single authenticated
   read-only call. A missing token is a disconnected account and is skipped
   without an attempt to connect; an explicit first connection stays a
   deliberate operator action.
4. Renew only if the verification failed. A successful renewal is recorded.
5. Spawn the worker child with the current interpreter and the inherited
   environment, and wait for it.
6. On child exit, read the exposure state back from the database and record the
   outcome, including the exit code.
7. Sleep until the next pre-open window.

The supervisor handles `SIGTERM` and `SIGINT` by forwarding the signal to the
child, waiting for it to finish, and then exiting. It must never leave an
orphaned worker behind, because an orphan would hold the lock and suppress the
next renewal in exactly the way this design exists to prevent.

If the supervisor container is restarted, Docker restarts the child with it.
The lock is released when the container's processes end, so a restart does not
leave a stale lock behind.

## Worker changes

### Clean session end

The worker gains a session-end predicate evaluated after every tick. The
session is finished when all of the following hold: the local time is 15:30 IST
or later, the account has no unresolved run, and the most recent run is in a
terminal state (`closed`, `rejected` or `skipped`) or does not exist.

Unresolved means any run with remaining quantity above zero or in
`entry_pending`, `exit_pending`, `open` or `reconciliation_required`, which is
the same definition the existing recovery path uses. When the predicate is
true the worker logs that the session completed with no exposure and exits
with status 0. When exposure is unresolved the worker keeps running and keeps
reconciling. It never exits while holding a position.

This is the change that satisfies the request to stop the worker after each
session. It applies equally to a worker started by the supervisor and to one
started by hand, so the behaviour does not depend on how the worker was
launched.

### Distinguishing an expired token from a network fault

`app/services/trading_market.py` currently converts every SDK failure into
`TradingUnavailable`, so an expired token is indistinguishable from a timeout.
A new `BrokerUnauthenticated` error is raised when the SDK reports HTTP 401. It
is not a subclass of `TradingUnavailable`, because the engine's existing
handler treats that error as "market data unavailable" and backs off quietly,
which is the behaviour that hid this failure for a whole session.

The engine must propagate `BrokerUnauthenticated` to the worker rather than
swallow it. Where a broker call may have been a mutation, the engine still
fails closed and marks the run `reconciliation_required` before propagating;
a 401 response is returned before the broker acts on a request, but the
outcome is treated as uncertain rather than assumed.

### Mid-session recovery

When the worker sees `BrokerUnauthenticated` it checks for open exposure.

With no exposure, the worker releases its own advisory lock, renews the token
through the existing PIN/TOTP flow, rebuilds the SDK client with the new token,
re-acquires the lock, and continues the session. The lock must be released
first because the renewal path acquires the account lock itself; if the lock
cannot be re-acquired afterwards, the worker fails closed and exits non-zero.

With exposure open, the default behaviour is to fail closed: the run becomes
`reconciliation_required`, the worker exits non-zero, and no further session
is started that day. An explicit override, `ORB_RENEW_WITH_EXPOSURE=true`,
allows the renewal anyway so the position can be managed. The override is off
by default, is logged as a warning whenever it is used, and is recorded in the
session record. This is the one case that remains manual by default, and the
residual risk is explicit: NRML carries no broker-side square-off guarantee,
so a position can remain open until an operator intervenes.

The 15:00 IST entry cutoff, the single entry allowance per session and the
existing risk limits are unchanged by this work.

## Failure handling

| Situation | Action | Surface |
| --- | --- | --- |
| Token invalid before the open, renewal fails | Skip the session. Retry about every 60 seconds until 09:15 IST, then stop attempting and wait for the next day. | Banner: session skipped, token renewal failed. |
| No saved token | Skip the session. An explicit first connection stays a manual `--connect` action. | Banner: account disconnected. |
| 401 during a session, no exposure | Renew in place and continue. | Log line only. |
| 401 during a session, exposure open | Fail closed by default: run becomes `reconciliation_required`, worker exits non-zero, no further session that day. | Banner: reconciliation required, plus a critical log. |
| Broker or network error that is not a 401 | Existing bounded backoff and session continuation. | Unchanged. |
| Worker child exits non-zero | Record the exit code. Retry the child at a slow cadence of about five minutes while the session window is open, and keep retrying after the close only while exposure is unresolved. Each retry re-reads the token from the database, so a manual renewal is picked up automatically. New entries stay blocked by the engine while any run is unresolved. | Banner: worker exited unexpectedly with the code. |
| Worker child exits 0 after the session | Normal close. | Banner: worker stopped, no exposure. |
| Supervisor container dies | Docker restarts it and the child with it. The next cycle resumes. | — |
| Exchange holiday | The worker starts, observes no normal session, idles, and exits at 15:30 without entering. | Banner: session closed, no trades. |

The supervisor never starts a trading session while an earlier session left
unresolved exposure. It may start the worker in that state only so the engine
can reconcile it, and the engine already refuses to consider new entries while
any run is unresolved.

A worker started by hand, outside the supervisor, still holds the account lock
and will therefore cause the renewal to be refused. That is the safe outcome,
and the recorded reason names it.

## Persistence and migration

A new table `strategy_sessions` holds one row per session date and mode, so
the dashboard can show the current session state without scanning run history.
The migration is Alembic revision `0009`, following `0008`.

Columns: `id`, `session_date`, `mode`, `phase`, `reason`, `token_state`,
`worker_exit_code`, `exposure_open`, `started_at`, `ended_at`, `created_at`,
`updated_at`, with a unique key on `session_date` and `mode`.

`phase` takes one of `preflight`, `token_valid`, `token_renewed`, `started`,
`closed`, `skipped`, `reconciling`, `failed`. `token_state` takes one of
`valid`, `renewed`, `invalid`, `missing`. `reason` uses a fixed vocabulary of
application messages and never contains broker response text, following the
existing convention that failures are described by the application rather than
by the exception.

The repository lives in `app/repositories/strategy_sessions.py`, named to stay
distinct from the existing web authentication module
`app/repositories/sessions.py`. Writes are idempotent upserts keyed on the
session date and mode.

## Dashboard surfacing

`app/services/strategy_status.py` reads the most recent session record for
the configured account and mode and exposes it on the `StrategyStatus`
projection. `app/templates/index.html` renders it as a banner beside the
existing heartbeat line, and `/strategy/history` shows the same note.

The banner states the outcome plainly: session skipped and why, worker stopped
with no exposure, reconciliation required, or an unexpected exit with its
code. The purpose is to remove the current false comfort of a current worker
heartbeat while every broker call is failing.

## Configuration

New environment settings, all with safe defaults:

| Variable | Default | Purpose |
| --- | --- | --- |
| `ORB_SESSION_START_IST` | `08:45` | When the pre-open window opens. |
| `ORB_SESSION_OPEN_IST` | `09:15` | Latest moment the worker may be started. |
| `ORB_SESSION_CLOSE_IST` | `15:30` | Earliest clean session end for the worker. |
| `ORB_RENEW_WITH_EXPOSURE` | `false` | Permit renewal while a position is open. |

The existing `ORB_EXECUTION_BACKEND`, limits, poll interval and quote freshness
settings are unchanged. The PIN and TOTP values must already be present on the
deployment host; the 21 October renewal used them.

## Deployment

`compose.yaml` drops the `token-scheduler` service, whose single renewal slot
is superseded by the supervisor's pre-flight, and replaces the `worker` service
with a `supervisor` service running `python -m app.cli.session_supervisor`
with `restart: unless-stopped` and a stop grace period long enough for the
child to shut down cleanly. The worker runs as a child process in the same
container, so no Docker socket, host cron entry or extra image is needed. The
web service is unchanged.

The rollout is a single deployment step, and it must complete before the next
pre-open window. The supervisor's first cycle will find today's expired token,
renew it through the PIN/TOTP path because the lock is free at that moment,
and start the worker. After that, no operator step is needed for ordinary
sessions.

`docs/operations/` gains a session lifecycle note describing the daily cycle,
the banner states, and the manual procedure for the exposure-blocked case.
`README.md` and `deploy/README.md` are updated to describe the supervisor and
to remove the now-obsolete instructions about stopping the worker before
renewal.

## Testing

All tests use fakes. No test places an order, and no test calls a live broker
endpoint.

New `tests/test_session_lifecycle.py` covers the pre-open window calculation
across weekdays, weekends, and times before and after the window, and the
session-end predicate for each of its conditions.

New `tests/test_session_supervisor.py` covers the ordering guarantees, that
renewal happens before the worker starts, that a failed renewal skips the
session without starting a worker, that a clean child exit is recorded and
followed by a sleep, and that a non-zero exit is recorded without starting a
new session while exposure is unresolved.

`tests/test_strategy_worker.py` gains cases for the clean session-end exit
returning status 0, a 401 without exposure leading to renewal and continued
operation, and a 401 with exposure leading to `reconciliation_required` and a
non-zero exit.

`tests/test_trading_market.py` gains cases that a 401 becomes
`BrokerUnauthenticated` and that other SDK failures remain
`TradingUnavailable`.

`tests/test_strategy_status.py` gains banner selection cases, and a MySQL
integration test covers the new table and its upsert behaviour.

## Out of scope

Changing the strategy itself, the entry or exit rules, the limits, the GTT
backend or the execution mode stored in `trading_config`. Enabling live
trading. Supporting multiple broker accounts or multiple sessions per day.
Reworking the authentication flow that issues the initial token.
