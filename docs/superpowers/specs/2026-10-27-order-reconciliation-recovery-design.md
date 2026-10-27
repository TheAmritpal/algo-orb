# Order Reconciliation Recovery Design

**Date:** 2026-10-27
**Status:** Proposed
**Scope:** live reconciliation recovery for the Oct 26, 2026 GTT run and prevention of the same failure mode.

## Problem

Run `160103` (`reliance_orb`, live, GTT, exchange date 2026-10-26) is stuck in
`reconciliation_required`. Its entry intent `7` has broker order
`261026000111597` and status `unknown`, with no stored fills. The worker polls
the order every 30 seconds and gets `404 UDAPI100010 Order not found`, because
Upstox removes order records at the end of the trading session.

The trade was not actually unknown:

- Entry: 1000 filled at average 45.775 (trades 370751, 370752).
- Exit: 1000 sold at 45.95 (trades 753152, 753153), manually executed after the
  operator cancelled the GTT legs.
- Broker position is flat. No order or trade exists for 2026-10-27.

The unresolved run blocks creation of a new daily run, so the live strategy
cannot place an order on the next session. The database must be repaired and
the failure mode must not recur.

## Goals

- Close run `160103` with auditable historical fills and zero remaining
  quantity, using the operator-confirmed manual exit.
- Allow the next session to create its daily run and submit an order when the
  strategy signals.
- Stop polling an expired prior-day order record forever.
- Preserve the safety rule that unresolved exposure or an active GTT still
  blocks new entries.
- Require an explicit operator attribution for historical repairs; never infer
  a sell order ID or invent fills from price/quantity alone.

## Non-goals

- No live order submission, modification, or cancellation during implementation
  or repair.
- No automatic repair of historical runs by matching price and quantity.
- No database schema change or new table.
- No frontend redesign.
- No assumption that public postbacks are authoritative.

## Evidence used

Authenticated Upstox reads and stored records for 2026-10-26:

- Postback: order `261026000111597` complete, 1000 filled, average 45.77.
- GTT `GTT-C26261000063564` ENTRY leg COMPLETED; both exit legs CANCELLED at
  11:13:55 IST after the operator manually cancelled them.
- Historical trades for 2026-10-26: BUY 500 @ 45.75 (370751), BUY 500 @ 45.80
  (370752), SELL 500 @ 45.95 (753152), SELL 500 @ 45.95 (753153).
- Current order book, today's trades, positions, and GTT details are empty.
- Upstox documents that orders remain available for one trading day and are
  removed at the end of the session.

## Design

### 1. Expired broker records

`sdk_data` preserves sanitised failure metadata only: exception category,
numeric HTTP status, and `UDAPI` error code. It never exposes response bodies,
messages, headers, or credentials. A prior-day exact-ID read that returns
`404` with `UDAPI100010` raises `BrokerOrderNotFound`, a subclass of
`TradingUnavailable`.

`OrderStatusPoller`:

- Detects `BrokerOrderNotFound` for an intent whose run exchange date is before
  today.
- Records one `order_status_expired` event containing the order ID, HTTP status,
  and broker code.
- Stops re-reading that order on later polls. While the run remains
  unresolved, it continues to check the broker position.
- Does not mark the run verified merely because the order record expired.

`StrategyEngine._manage` handles the poll outcome:

- If any order read failed for a reason other than expiry, fail closed as today.
- If an expired record is present, read the broker position for the run
  contract and product `D`, and read the stored GTT snapshot.
- Park the run in `reconciliation_expired` only when the position is flat and
  the GTT snapshot shows the ENTRY leg `COMPLETED` and every exit leg terminal.
  For non-GTT runs, a flat position and no working exit intent is sufficient.
- Otherwise keep `reconciliation_required` and the existing blocking behavior.
  The message distinguishes expired records from other failures.

`reconciliation_expired` is added to the repository's valid states and excluded
from `list_unresolved`, because it has been proven to have no exposure. It is
not a substitute for `closed`; it is a parked state requiring historical
repair. The run history keeps showing the parked run and its message.

The operator manifest for this incident is:

```json
{
  "run_id": 160103,
  "orders": [
    {
      "purpose": "entry",
      "order_id": "261026000111597",
      "trade_ids": ["370751", "370752"]
    },
    {
      "purpose": "exit",
      "trade_ids": ["753152", "753153"]
    }
  ]
}
```

### 2. Historical repair service

`app/services/historical_reconciliation.py`:

- `historical_trades(client, exchange_date)` pages through
  `PostTradeApi.get_trades_by_date_range` for the exchange date, segment `FO`.
- `build_repair(run, manifest, trades, now)` validates and constructs the
  repair. The manifest is an operator assertion, not a source of fills.
- Validation:
  - live, GTT, entry-reserved, prior-day run in
    `reconciliation_required`, `reconciliation_expired`, or `closed`;
  - manifest run ID matches;
  - exactly one entry and one exit order;
  - entry order ID matches the stored entry intent and the GTT snapshot ENTRY
    leg; the exit order ID is optional and may be absent;
  - each trade ID is unique, belongs to the run date and contract
    (`RELIANCE`, option type, strike, expiry), has the correct side, and the
    total quantity equals the run's requested quantity;
  - no trade is assigned twice.
- `verify_callbacks(run, repairs, callbacks)` cross-checks stored postbacks when
  present. A missing postback does not invalidate an explicit operator
  attribution. A contradictory postback rejects the repair.

### 3. Repository repair

`StrategyRepository.repair_closed(run, repairs, *, apply=False)`:

- Dry run performs all validations and returns the projected run and orders with
  no writes.
- Apply runs in one transaction:
  - lock and re-read the run, verify the version and state;
  - reject any pending GTT mutation;
  - reject a missing or conflicting GTT parent-child identity;
  - reject an entry order owned by another run or conflicting stored fills;
  - complete the entry intent with its fills;
  - insert the historical exit intent with `broker_id = NULL`, a unique
    `hist…` tag, and synthetic `hist…` fill order IDs;
  - recompute the run with `reduce_fills`, set state `closed`, and save with the
    expected version;
  - append a `historical_reconciled` event with the order IDs and trade IDs;
  - commit, or roll back the whole repair on any error.
- Repeating an identical repair is idempotent and writes nothing.

### 4. Operator CLI

`app/cli/reconcile_history.py`:

- Input: a JSON manifest with `run_id` and `orders`.
- Authenticates with the stored live token, verifies the account matches the
  run, reads historical trades, builds and verifies the repair, and requires a
  flat broker position.
- Dry run is the default and writes nothing.
- `--apply` acquires the live worker advisory lock, re-reads the run after
  acquiring it, and applies. If the worker is running, it refuses.
- Makes no broker mutations. Prints a JSON summary or sanitised failure
  metadata without secrets or broker bodies.

### 5. Production operation

- Operator stops the production worker before apply.
- Run the CLI dry run, review the projected result, then apply.
- Verify:
  - run `160103` is `closed`, filled 1000, remaining 0, entry average 45.775;
  - entry intent complete with trades 370751/370752;
  - historical exit intent complete with trades 753152/753153 and no broker ID;
  - four fill rows, unique trade IDs;
  - a `historical_reconciled` event exists;
  - broker position remains flat.
- Restart the worker after the 15:30 IST session close. The current day is
  recorded as skipped; the next session starts clean.
- The DB repair alone restores next-session trading. The new code prevents the
  next expiry from blocking and must be deployed separately.

## Error handling

- Broker/SQL exception text is never logged or printed.
- Only fixed operation names, exception categories, numeric HTTP statuses, and
  validated `UDAPI` codes are persisted in diagnostics.
- Any inconsistency or uncertainty refuses the repair and leaves the run
  unchanged.
- A non-flat position or active GTT never becomes `reconciliation_expired`.

## Testing

- Poller: expired prior-day order with flat position and terminal GTT parks;
  non-flat position blocks; active GTT blocks; non-404 failure remains
  unverified; expired order is not re-read on every poll.
- Repair service: manifest identity, date/contract/side/quantity checks,
  duplicate trade IDs, conflicting callbacks, missing sell order ID.
- Repository: dry-run has no writes; apply rollback on conflict; idempotence;
  atomic run/orders/fills/event update; stale version; pending mutation.
- CLI: dry-run is read-only; apply requires the worker lock; wrong account and
  non-flat position refuse; resources close.
- Full existing unittest suite.

## Acceptance criteria

- Run `160103` is `closed` with four auditable fills and zero remaining.
- The next session creates a new run and can submit an order when the strategy
  signals.
- A prior-day expired order no longer produces unbounded 30-second polling.
- A prior-day expired order with open exposure or an active GTT still blocks.
- No live order is submitted, modified, or cancelled during the work.
