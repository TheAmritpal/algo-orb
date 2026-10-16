# RELIANCE ORB Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement the approved RELIANCE NRML two-lot breakout strategy with premium-based risk, durable daily limits, app-managed 15:25 IST exits, and dashboard visibility.

**Architecture:** Pure strategy rules feed a separate synchronous worker. MySQL persists intent before each broker mutation; an official-SDK adapter reconciles fills and positions, while a paper adapter permits deterministic testing. FastAPI renders read-only strategy status and never starts the worker.

**Tech Stack:** Python >=3.13, uv, Uvicorn, FastAPI, Jinja2, PyMySQL, Alembic/SQLAlchemy, official upstox-python-sdk, standard-library unittest and Decimal.

**Spec:** docs/superpowers/specs/2026-10-16-reliance-orb-design.md (approved by user on 2026-10-16).

## Global Constraints

- Use Asia/Kolkata exchange time and RELIANCE equity candles for signals.
- The earliest signal is 09:45. No signal means no entry.
- Buy two lots of the nearest available monthly RELIANCE option expiry using NRML, represented by SDK product D.
- Exit at an option premium ten rupees below the actual weighted average entry fill, twenty rupees above it, or initiate square-off at 15:25 IST, whichever applies first.
- One entry attempt per account, strategy, and exchange date, across both directions, including an ultimately rejected entry.
- The last-entry submission cutoff is 15:00 IST.
- Live execution remains disabled by default.
- Do not call an unfiltered exit-all-positions API.
- Do not replace the installed package with this checkout automatically.
- Development/testing uses mocks and paper fills; do not launch a live worker, submit live orders, or apply live database migrations.
- Missing required monetary limits prevents live entry.

## Review Focus

1. Malformed broker numbers (NaN, infinity, negative prices, fractional lot sizes): reject without submitting an order; Task 1/3 tests.
2. Repeated/out-of-order candles and cumulative fills: normalize inputs and count each broker fill once; Task 1/4 tests.
3. Worker lock connection loss during a broker call: a replacement reconciles the persisted intent before trading; Task 2/4 tests.
4. Delayed stop cancellation that fills meanwhile: recompute exposure before the next sell; Task 4 tests.
5. NRML exposure surviving overnight or a token/account change: recover the original run without switching accounts or claiming it closed; Task 4/5 tests.

## File map

Create app/strategies/{__init__,models,rules,engine}.py for domain types, pure decisions, and orchestration. Create app/core/trading.py for validated settings; app/repositories/{strategy,worker_lock}.py for transactional persistence and lock lifetime; app/services/{trading_market,trading_broker,paper_broker,strategy_status}.py for SDK boundaries, paper execution, and dashboard projection; app/cli/run_strategy.py for the worker. Add migrations/versions/0003_create_strategy_state.py. Modify app/routers/pages.py, app/templates/index.html, app/static/app.css, .env.example, and README.md. Add the task-specific tests below and extend tests/test_migrations.py. No unrelated OAuth/authentication refactor.

## Shared domain contract

Task 1 defines frozen dataclasses and enums in app/strategies/models.py. Use timezone-aware datetime, date exchange dates, and finite Decimal monetary values.

- Mode: paper or live; Direction: CE or PE; Purpose: entry, stop, or exit.
- Candle(start, open, high, low, close); OpeningRange(high, low); Signal(direction, candle_close).
- Contract(instrument_key, underlying_key, expiry, weekly, strike, option_type, lot_size, tick_size).
- Quote(instrument_key, timestamp, bid, ask, last); Fill(trade_id, order_id, quantity, price).
- RunKey(client_id, user_id, strategy, mode, exchange_date); StrategyRun includes ID, key, version, state, range, processed candle, contract, requested/filled/remaining quantities, entry_average, stop, target, heartbeat, and sanitized message.
- OrderIntent includes unique tag, run ID, purpose, instrument, D product, side, quantity, limit/trigger prices, submission timestamp, broker ID, status, and fills.
- BrokerOrder includes broker ID/tag, normalized status, requested/filled quantities, and fills; BrokerPosition includes instrument, product, and net quantity.
- MarketSnapshot(now, normal_session, candles, contracts, underlying_quote, option_quote).
- EntryLimits(max_premium, max_notional, max_planned_loss, max_spread, max_slippage); OrderRequest(tag, instrument, product, side, order_type, quantity, price, trigger_price).
- Reject invalid prices, timestamps, quantities, sides, and unknown broker statuses at boundaries. Preserve unknown outcomes explicitly rather than converting them to rejection.

### Task 1: Pure RELIANCE rules and validated settings

**Files:** Create app/strategies/__init__.py, models.py, rules.py, app/core/trading.py; modify .env.example; tests/test_strategy_rules.py and tests/test_trading_settings.py.

**Interfaces:** Produce build_range(candles: list[Candle], now: datetime) -> OpeningRange | None; breakout(candles: list[Candle], opening: OpeningRange, now: datetime, after: datetime | None) -> Signal | None; select_contract(contracts: list[Contract], spot: Decimal, direction: Direction, today: date) -> Contract; premium_levels(average: Decimal, tick: Decimal) -> tuple[Decimal, Decimal]; exit_reason(now: datetime, quote: Quote | None, stop: Decimal, target: Decimal) -> str | None. Define TradingSettings and load_trading_settings() -> TradingSettings.

- [ ] Write failing unittest cases asserting opening range is unavailable at 09:29:59 and available at 09:30, strict close breaks signal at 09:45, wicks/equality do not signal, no entry after 15:00, candle age >60 seconds/missing intervening candles/old dates do not signal. Duplicate candles normalize deterministically; conflicting duplicate OHLC rejects the snapshot.
- [ ] Add contract tests using spot 1500: calls select 1480 and puts 1520 when those are nearest strict ITM strikes; exclude weekly, expired, wrong-underlying and wrong-type contracts. Test 250- and 500-unit lots produce quantities 500 and 1000. Test entry average 100 gives stop 90 and target 120, average <=10 rejects entry, and nonaligned levels reject rather than silently changing the requested risk.
- [ ] Add settings/boundary tests: default paper, live disabled, required explicit broker user, optional paper synthetic account, finite positive monetary limits required for live, reject NaN/infinity, zero/negative tick size, fractional lots, naive times, unknown mode and invalid booleans. Configure ORB_MODE, ORB_LIVE_ENABLED, ORB_BROKER_USER_ID, ORB_MAX_PREMIUM, ORB_MAX_NOTIONAL, ORB_MAX_PLANNED_LOSS, ORB_MAX_SPREAD, ORB_MAX_SLIPPAGE, ORB_QUOTE_MAX_AGE_SECONDS (default 5), and ORB_POLL_SECONDS (default 2, minimum 2). Timing/risk/lot rules remain fixed from the approved spec.
- [ ] Run `uv run python -m unittest tests.test_strategy_rules tests.test_trading_settings -v`; confirm the new cases fail before implementation.
- [ ] Implement domain types, rules, settings and placeholder-only .env.example entries. Enforce sorted current-session 15-minute candles, strict ITM, metadata lot sizes, Decimal arithmetic and timestamp validation. A stale quote disables entry/price-based signals but never disables a due time exit.
- [ ] Repeat the targeted command; require all cases pass. Commit only this task's files as `feat: add RELIANCE ORB rules and settings`.

### Task 2: Durable runs, order intents, events and worker ownership

**Files:** Create migrations/versions/0003_create_strategy_state.py, app/repositories/strategy.py, app/repositories/worker_lock.py; extend tests/test_migrations.py; create tests/test_strategy_repository.py and tests/test_worker_lock.py.

**Interfaces:** Produce StrategyRepository.get_or_create(key: RunKey) -> StrategyRun; list_unresolved(client_id: str, user_id: str, mode: Mode) -> list[StrategyRun]; reserve_entry(run_id: int, expected_version: int, request: OrderRequest) -> OrderIntent | None; create_intent(run_id: int, expected_version: int, purpose: Purpose, request: OrderRequest) -> OrderIntent; apply_order(intent_id: int, order: BrokerOrder) -> StrategyRun; heartbeat(run_id: int, now: datetime) -> None; transition(run_id: int, expected_version: int, state: str, changes: dict) -> StrategyRun; append_event(run_id: int, kind: str, fields: dict) -> None; latest(client_id: str, user_id: str, mode: Mode) -> StrategyRun | None. Produce WorkerLock.acquire() -> bool, assert_owned() -> None, close() -> None.

- [ ] Write failing tests for unique account/strategy/mode/date reservation, concurrent entry attempts yielding one intent, rejected entries consuming allowance, version conflicts, stale state rejection, rollback, and paper/live separation. Replay the same trade ID twice and assert filled quantity and entry average stay unchanged.
- [ ] Add lock tests for duplicate worker refusal, hashed account/strategy lock names within MySQL's limit, dedicated connection lifetime, loss detection with CONNECTION_ID/IS_USED_LOCK and no automatic reconnect, plus resource cleanup. Test a disconnected ownership connection forbids mutations even when repository connections still work.
- [ ] Extend offline Alembic tests for strategy_runs, strategy_orders, strategy_fills (unique broker account/mode/trade ID), strategy_events, unique run key/tag and FK/index ordering; downgrade 0003:0002 preserves auth/token tables. Run targeted unittest modules and establish failures.
- [ ] Implement InnoDB schema with UTC DATETIME(6), DECIMAL(20,8), parameterized SQL, row locks, version checks, finite state validation, unique intents and fill deduplication. Store run data in explicit columns and allowlisted request/response JSON. Accept skipped/rejected/closed as terminal daily states; unresolved exposure remains recoverable across dates. Never store tokens in these tables or raw exceptions in events. Validate/reuse matching partial migration tables and refuse mismatches, following 0002's approach.
- [ ] Run targeted repository/lock/migration tests; require pass. Commit as `feat: persist strategy execution and worker ownership`.

### Task 3: Official SDK adapters and deterministic paper execution

**Files:** Create app/services/trading_market.py, trading_broker.py, paper_broker.py; extend app/repositories/tokens.py with get_token(client_id: str, user_id: str) -> UpstoxToken | None; create tests/test_trading_market.py, test_trading_broker.py, test_paper_broker.py; extend tests/test_tokens.py.

**Interfaces:** Produce MarketData.snapshot(now: datetime, contract: Contract | None) -> MarketSnapshot; Broker.submit(request: OrderRequest) -> BrokerOrder; cancel(order_id: str) -> BrokerOrder; reconcile(intent: OrderIntent) -> BrokerOrder | None; positions() -> list[BrokerPosition]; available_funds() -> Decimal; supports_protective_stop(contract: Contract) -> bool; close() -> None. PaperBroker implements the same broker interface with mode-isolated fill IDs and restart state reconstructed from persisted intents. A missing reconciliation result means unknown, never permission to resubmit.

- [ ] Write mocked contract tests for current equity candles, fresh bid/ask and exchange times/holidays, parsing timestamps/finite decimals, RELIANCE identity, lot/tick/expiry/weekly metadata and mismatched symbols. Invalid or absent normal-session information blocks entry. Verify actual installed SDK signatures against local reference docs and record discrepancies in README; do not modify dependencies to match assumptions.
- [ ] Write adapter tests asserting D product, two metadata lots, correct unique tag, bounded LIMIT prices, buy quantity and stop SELL SL trigger/limit ordering. Assert insufficient funds, excess notional/planned loss/spread, stale quotes, malformed values and missing stop support block live entry. Mock API timeout after accepted submission: outcome remains unknown and reconcile uses tag/history/fills/positions before any new submission. Assert all SDK calls have bounded timeouts and no automatic mutation retries.
- [ ] Add exact-account token tests and cleanup/error sanitation tests. Add paper tests for deterministic ask-side entry, bid-side sell, stop/target crossings, partial/replayed fills and restart without duplicate fills. Never present paper executions as broker confirmations. Run targeted tests and establish failures.
- [ ] Implement adapters using verified installed capabilities: OptionsApi.get_option_contracts; HistoryV3Api.get_intra_day_candle_data; MarketQuoteApi.get_full_market_quote; MarketHolidaysAndTimingsApi session/holiday methods; OrderApi placement, cancellation, book/history and trade methods; PortfolioApi.get_positions. Prefer one supported order API version consistently. Resolve RELIANCE via validated configured equity instrument metadata; fail closed if unresolved. Authenticate using get_token for the pinned user, not get_latest_token. Throttle polling/read batches with bounded read backoff; close SDK pool resources. If no sandbox-supported stop capability can be established, keep live entries unavailable and report the specific limitation without probing by placing a live order.
- [ ] Repeat targeted tests and require pass. Commit as `feat: add broker and paper execution adapters`.

### Task 4: Restart-safe order lifecycle and exits

**Files:** Create app/strategies/engine.py; create tests/test_strategy_engine.py using in-memory repository, fake clock, market data and broker doubles.

**Interfaces:** Produce StrategyEngine(settings: TradingSettings, repository: StrategyRepository, market: MarketData, broker: Broker, ownership: WorkerLock); tick(now: datetime) -> None; recover(now: datetime) -> None; stop_entries() -> None. Engine consumes only Task 1-3 interfaces; it does not import HTTP routes or instantiate SDK clients itself.

- [ ] Write failing lifecycle cases: complete bullish/bearish paper sessions, first entry only, rejected entry consumes day, actual fill average drives levels, restart before/after broker response, cumulative partial fills, late fills after cancel request, rejected protective stop initiates confirmed-quantity closure, no top-up, wrong-account reconciliation failure, manual positions block attribution, and unrelated positions untouched.
- [ ] Write exit race tests: stop fills during cancellation; cancellation times out; target and 15:25 occur together; accepted exit still pending; partial exit then terminal status; loss of ownership after intent persistence but before response. Assert total SELL fills <= total BUY fills and unknown outcomes produce zero replacement submissions. Confirm a protective stop is installed promptly on confirmed partial entry quantity and resizing waits for old stop cancellation reconciliation.
- [ ] Add time/recovery tests at 15:24:59, 15:25 and restart at 15:26 with no quote, stale quote, or expired token. At deadline cancel/reconcile pending entry remainder, reconcile protective stop, then exit confirmed remaining quantity; unavailable pricing/broker/closed exchange keeps visible unresolved exposure. Prior-day exposure blocks new-day entries until reconciled. Closing never depends on an active website session or entry enablement. Run engine tests and establish failures.
- [ ] Implement deterministic checked transitions and persisted intents before each mutation, confirming lock immediately before mutation. Reconcile open/unresolved runs first, compute remaining quantity from unique fills and compare positions. Keep at most one unresolved sell intent for a given exposure. For stop replacement or target/time exit, reconcile terminal cancellation and any intervening fills before another sell. A timeout, DB failure after submission, impossible quantity or manual mismatch enters reconciliation_required and preserves the intent for recovery. No global exit call and no blind entry/exit retry.
- [ ] Run engine plus adapter/repository tests; require pass. Commit as `feat: orchestrate durable RELIANCE entries and exits`.

### Task 5: Separate supervised worker entry point

**Files:** Create app/cli/run_strategy.py; tests/test_strategy_worker.py; modify README.md worker section.

**Interfaces:** Produce run_worker(settings: TradingSettings, stop: threading.Event) -> int and main() -> int; invoke through `uv run python -m app.cli.run_strategy`. Injection seams allow fake clock/sleep/engine in tests; production instantiates Task 1-4 components.

- [ ] Write failing tests for paper default, explicit live enablement and limits, absent exact-account token, missing DB schema, duplicate worker, read backoff capped at 30 seconds, no catch-up entry, heartbeat updates and SIGTERM during an open NRML run. Simulate changing latest-token account: worker remains bound to its configured original user. Importing app.main must never create a worker or issue orders.
- [ ] Run targeted worker tests and establish failures.
- [ ] Implement normal-session polling no faster than configured interval with separate read retry backoff, immediate recovery on start, UTC persisted heartbeats and graceful signal handling. Shutdown disables entries, persists exposure and unresolved intents, closes resources and reports remaining exposure; it must not falsely claim that stopping the worker schedules future exits. Exit due checks occur before optional market reads. DB/lock loss stops submissions; supervision/restart recovers state. Do not run a worker during development unless using test doubles.
- [ ] Run targeted worker/engine tests and require pass. Document required external supervision and the need for worker availability at 15:25. Commit as `feat: add standalone ORB strategy worker`.

### Task 6: Authenticated dashboard status

**Files:** Create app/services/strategy_status.py; modify app/routers/pages.py, app/templates/index.html, app/static/app.css; create tests/test_strategy_status.py; update existing dashboard mocks in tests/test_upstox_status.py as needed.

**Interfaces:** Produce get_strategy_status() -> StrategyStatus, a sanitized projection including static rules, mode, latest run, worker freshness and exposure; it reads persistence only. Extend home template context with strategy, preserving session and upstox fields.

- [ ] Write failing tests for unauthenticated access, empty initial state, paper-vs-live label, range/contract/fill/stop/target/remaining quantity/entry allowance, 15:25 deadline, 3:00 cutoff, heartbeat >30 seconds stale, DB unavailable and unresolved overnight exposure. Inject secret-looking exception strings and assert they never render. Assert no order SDK methods are called while loading dashboard.
- [ ] Run targeted status/auth/dashboard tests and establish failures.
- [ ] Implement status projection and responsive Jinja2 panel using existing styling. Show disconnected/stale worker explicitly; configuration is not evidence that the worker runs. Preserve broker OAuth/login/logout behavior. Do not add start-trading or order buttons.
- [ ] Run status and existing auth/OAuth/dashboard tests; require pass. Commit as `feat: display RELIANCE strategy and execution status`.

### Task 7: Whole-flow verification and operations documentation

**Files:** Create tests/test_strategy_session.py; modify README.md and .env.example as needed. No new dependencies without demonstrated need.

**Interfaces:** Consume all prior task contracts. Produce a deterministic paper session and restart/exit integration test that uses production engine and adapters with mocked SDK/SQL boundaries.

- [ ] Write and run failing integration tests for normal call/put days, no-breakout day, rejection, partial entry with restart, unknown timeout resolved by tag, stop/target race and 15:25 exit. Assert all mutation requests in tests go only to mocks/paper, no duplicate intent tags and no oversell. Add a session using actual SDK response model objects to catch field/signature drift.
- [ ] Implement only necessary integration fixes, preserving task interfaces; repeat session tests and require pass.
- [ ] Document dependencies/install commands, exact-account binding, offline migration preview, explicit migration command, worker/paper startup, live-disabled default, monetary limit units, protective-stop prerequisites, fill/price-bound execution limitations, manual-position restrictions, account recovery, NRML exposure after outage, and stop/cancel reconciliation workflow. Preserve existing .env secrets; only edit .env.example placeholders. State that applying migrations and enabling live execution remain distinct deployment actions.
- [ ] Run `uv run python -m unittest discover -v`; require all tests pass without network credentials. Run `uv run alembic upgrade head --sql` and check 0003 tables/constraints and no connections/secrets. Run `git diff --check`. Record commands, outputs, SDK limitations, and checks not executed. Use approved escalation for any sandbox writes; do not bypass filesystem permissions.
- [ ] Review all spec sections against code and tests, especially no replayed entries, one daily reservation, shared exit quantities, lock loss, stale price at timed exit and unresolved exposure. Request branch review through the applicable review skill only after tests pass, resolve findings, and commit task files as `test: verify ORB sessions and document operations`. Do not merge, deploy, migrate the live database, or launch live execution as part of verification.

## Plan self-review

Coverage: rules/settings (Task 1); durable state/concurrency/schema (Task 2); SDK, token binding, funds/limits, paper and broker responses (Task 3); partial fills, stop lifecycle, uncertain outcomes, daily allowance and timed exits (Task 4); worker availability/shutdown/session calendar (Task 5); authenticated observability (Task 6); end-to-end checks and delivery (Task 7). All five Review Focus items have named cases in their owning tasks. Shared methods/types are defined before consumption. No implementation has begun.

## Handoff

Plan awaits user review and execution-method selection. Native execution means the current agent implements the tasks sequentially using executing-plans. Subagent-driven execution delegates tasks and reviews using subagent-driven-development. No method has yet been selected, and no subagents have been spawned.
