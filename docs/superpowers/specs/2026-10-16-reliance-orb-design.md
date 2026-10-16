# RELIANCE opening range breakout

Date: 2026-10-16
Status: Proposed written design; awaiting review before implementation planning.

## Intent and agreed trading rules

Add a RELIANCE equity opening range breakout strategy to the existing Algo ORB application. Preserve FastAPI, Uvicorn, uv, Jinja2, MySQL, Alembic, and the official Upstox Python SDK. This work implements the strategy; it does not authorize a live order during development or testing.

Use Asia/Kolkata exchange time and RELIANCE equity candles for signals. The first regular-session candle covers 09:15 inclusive to 09:30 exclusive. Persist its high and low only after it completes. A subsequent completed 15-minute candle closing strictly above the high signals a call purchase; strictly below the low signals a put purchase. Equality and wick-only breaks do not signal. The earliest signal is 09:45. No signal means no entry.

Buy two lots of the nearest available monthly RELIANCE option expiry using NRML, represented by SDK product D. Resolve current contracts, expiry dates, strike prices, tick sizes, and lot sizes from broker metadata instead of hard-coding calendar rules or quantity. At entry use a fresh underlying quote: choose the highest available call strike strictly below spot, or the lowest available put strike strictly above spot. Quantity is twice that selected contract's lot size.

Exit at an option premium ten rupees below the actual weighted average entry fill, twenty rupees above it, or initiate square-off at 15:25 IST, whichever applies first. Prices use Decimal and broker tick-size validation. A position is closed only after confirmed fills, not after an accepted submission. The 15:25 deadline is an app-managed exit because NRML is carry-forward; it is not a guarantee of broker square-off or execution at that exact time.

One entry attempt per account, strategy, and exchange date, across both directions, including an ultimately rejected entry. This conservative interpretation prevents repeated entries; there is no reversal, top-up after a partial fill, or re-entry after exit.

## Proposed operational choices for review

The last-entry submission cutoff is 15:00 IST. Skip any signal whose entry cannot be submitted by that cutoff; a candle received after 15:00 cannot authorize a new entry. Do not replay old signals on startup: accept only a newly completed candle observed within 60 seconds of its close, with a complete opening range and no missing preceding candles. A missed signal is recorded as skipped. This does not prevent reconciling existing orders or exiting an existing position.

Support normal NSE sessions only initially. Check broker holidays and session times; skip entries on holidays, special sessions, absent or stale market data, or unavailable contracts. Keep nearest-month selection on expiry day as requested; reject contracts that the broker restricts, with no automatic expiry substitution.

Live execution remains disabled by default. Add a paper mode using the same strategy decisions and database state machine but a simulated execution adapter; store its mode separately so paper activity never consumes the live entry allowance. Do not launch a live worker as part of application startup or automated tests. No new browser action silently enables live trading.

## Architecture and alternatives

Recommended: a separate Python strategy worker with persisted state and SDK adapters, plus an authenticated dashboard status panel. It can operate independently of browser requests and Uvicorn reloads. A background task inside FastAPI is simpler but couples position management to web reloads and worker count. A signal-only panel is smaller but cannot satisfy the required automatic exits. Implement the separate worker.

Keep pure candle, contract selection, and premium-risk decisions under app/strategies/. Put SDK market-data and order calls behind app/services/ adapters. Put parameterized MySQL persistence in app/repositories/ and the worker entry point under app/cli/. Keep routes read-only for strategy status and render the existing dashboard with Jinja2. Do not place broker operations or schema changes in request handlers.

Use a bounded polling loop for completed candles, quotes, orders, and positions. Enforce SDK timeouts, quote freshness, documented API rate limits, and bounded backoff for read failures. Broker interaction must use capabilities supported by the installed SDK, verified against the local reference checkout at /home/amritpal/Downloads/upstox-python-master. Do not replace the installed package with this checkout automatically.

## Durable state and concurrent execution

Use an Alembic revision after 0002 for daily strategy runs, order intents, and sanitized append-only events. Daily runs have a unique key for broker app/account, strategy, mode, and exchange date. Persist opening range, processed candle timestamp, selected contract, quantity, actual entry average, exit levels, remaining quantity, state, and worker heartbeat. Use UTC database timestamps and an explicit IST exchange date.

Reserve the entry allowance transactionally before any broker mutation. Persist a unique intent tag and request fields before submission. Track order purpose, broker ID, requested/filled quantities, status, and sanitized responses. A single account/strategy worker holds a MySQL advisory lock on a dedicated connection. Losing that connection disables further submissions; a replacement worker reconciles persisted pending intents before it can trade.

Suggested states are waiting_range, waiting_signal, entry_pending, open, exit_pending, closed, skipped, rejected, and reconciliation_required. State transitions must be transactional and checked. Recover previous-day open runs before considering a new day's entry. Bind a run to its original broker account; never switch an open position to a newly connected account.

## Orders, exits, and uncertain outcomes

Validate contract identity, D product, two-lot maximum, lot and tick multiples, available funds, fresh quotes, market session, and configured premium/notional and loss limits before entry. Missing required monetary limits prevents live entry. Use price-bounded limit orders, with configured maximum spread and slippage limits. A ten-point stop is a trigger rule and cannot guarantee a ten-point maximum realized loss.

For live entry, cancel any unfilled remainder after a bounded wait, confirm terminal state, and manage the confirmed partial quantity without topping up. Track premium risk as soon as fills arrive. Require broker-held protective-stop support before enabling live entries and place a protective stop for confirmed quantity. If protection is rejected after a fill, reconcile and initiate closure of that confirmed quantity rather than leave it unmanaged. Let the worker monitor the target; do not maintain two independent full-size sell orders without broker-guaranteed OCO behavior. Before a target or timed exit, cancel the protective stop, confirm its terminal status and any intervening fills, then submit only the confirmed remaining quantity. Never blindly cancel and replace after a timeout.

After submission uncertainty, reconcile by order ID or unique tag using order history/book and trade fills, and compare broker positions. Do not resubmit while the original outcome is unknown. Exit retries require confirmed terminal status of prior attempts and recalculation of remaining quantity. At 15:25 stop entries, cancel and reconcile any outstanding entry remainder, then exit any confirmed strategy quantity. Handle partial exit fills, price-limit failures, and exchange closure explicitly. Keep an unresolved exposure visible as reconciliation_required; never report it closed merely because time elapsed.

Exit only this strategy's contract and quantity. Do not call an unfiltered exit-all-positions API. Reject entry if an existing position in the selected contract would prevent reliable attribution, and halt automatic quantity changes when manual trades cause a reconciliation mismatch. Disabling entries must not disable management of an existing position.

## Dashboard and observability

Display RELIANCE, NRML, two lots and resolved units, expiry/strike/type, opening range, last completed signal candle, run mode, daily allowance, order/fill state, entry average, premium stop/target, 15:25 exit deadline, heartbeat, and sanitized failures. Show stale worker or unresolved exposure clearly. Do not imply paper fills are broker fills or that a stopped worker can perform scheduled exits.

Persist allowlisted order request and response fields and status events for troubleshooting. Exclude access tokens, Authorization headers, credentials, raw SDK exceptions, cookies, and SQL parameter values from logs and pages.

## Verification and delivery

Use unittest and mocked SDK/MySQL boundaries, a fake clock, and deterministic paper fills. Cover candle boundaries and earliest signal, equality and wick rejection, stale or missing candles, calls and puts, strict ITM selection at exact strikes, nearest actual monthly expiry, lot-size changes, D product, two lots, fill-based stop/target, partial fills, daily uniqueness, concurrent workers, restart recovery, unknown broker outcomes, exit cancellation races, 15:25 time exits, previous-day exposure, account changes, and preservation of unrelated positions. Assert no second sell can exceed the remaining strategy quantity.

Verify migration SQL generation, existing authentication and OAuth tests, dashboard authentication and secret-free errors, worker shutdown, paper mode, and the live-disabled default. Run no live orders or live database migrations in tests. Document uv setup, migration commands, worker operation and supervision, trading-limit settings, paper limitations, and reconciliation steps. Record which checks and setup commands were actually run.

The next stage is written-spec review, then an implementation plan under docs/superpowers/plans/ and selection of execution method. Product implementation has not started.
