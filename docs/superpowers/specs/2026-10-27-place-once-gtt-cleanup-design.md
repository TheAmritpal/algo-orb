# Place-Once GTT with 15:14 Cleanup Design

**Date:** 2026-10-27
**Status:** Proposed
**Supersedes:** `2026-10-27-order-reconciliation-recovery-design.md`

## Goal

Place one intraday GTT when the first 15-minute opening-range breakout is confirmed, let Upstox manage the entry, stop, and target during the day, and perform one cleanup at **15:14 IST**. Do not poll order status every 30 seconds and do not continuously reconcile fills.

The next session must create a fresh run and place its GTT even when the previous day's order records are no longer available from Upstox.

## Current problem

Run `160103` (2026-10-26, live GTT, product `D`) is stuck in `reconciliation_required` with entry order `261026000111597` status `unknown`. The entry actually filled (1000 at average 45.775) and the operator manually exited (1000 at 45.95), leaving the account flat. The 30-second poller now receives `404 UDAPI100010` because Upstox removes order records at the end of the trading day, so the run can never resolve and blocks new entries.

## Design

### 1. Entry

- Keep the existing first-15-minute ORB signal, contract selection, quote freshness, funds, and trading-limit validation.
- Place one `MULTIPLE` GTT with **product `I` (MIS/intraday)**:
  - ENTRY at the current ask (`IMMEDIATE`),
  - STOPLOSS at entry − 5 premium points,
  - TARGET at entry + 8 premium points.
- The initial stop and target are final. With no fill tracking, the GTT is not modified after the actual fill average.
- After the GTT acknowledgement, store the GTT ID in `strategy_gtts` and transition the run to `gtt_active`.
- Do not poll order status, attach child intents, sync fills, retry the entry, or modify the GTT during the day.

### 2. 15:14 cleanup

The worker runs the cleanup once when the IST clock reaches 15:14.

1. Read the GTT details once.
2. Cancel the GTT parent and any working child order linked to its rules.
3. Read the position for the run contract and product `I`.
4. If the position is flat, transition the run to `closed` with “Day-end cleanup: flat”.
5. If the position is non-zero:
   - transition the run to `closing` and set `filled_quantity` / `remaining_quantity` from the actual position;
   - create one exit intent with product `I`, a unique tag, and the actual quantity;
   - submit one bounded LIMIT SELL;
   - record the broker order ID on the intent;
   - transition the run to `closed` with “Exit submitted at 15:14; MIS auto-square-off backstop”.
6. If the GTT read/cancel is uncertain, or the exit submission is rejected/uncertain, transition to `reconciliation_required` and emit an alert event. Do not submit a second sell.
7. Do not poll the exit order afterwards. Upstox MIS auto-square-off around 3:20–3:25 PM is the backstop.

The cleanup is idempotent:

- `gtt_active`: perform the cancel/position/sell flow.
- `closing` or `exit_pending` with a recorded exit broker ID: mark the run `closed` without another sell.
- `closing` or `exit_pending` without a broker ID: fail closed as “exit submission outcome unknown” and require manual checking. Never submit a duplicate sell.
- `closed`: no action.

### 3. Product support

The domain and broker adapters currently hard-code product `D`. They must accept both `I` and `D`:

- `OrderRequest` accepts `"I"` and `"D"`.
- `Broker.submit`, `Broker.reconcile`, and the GTT broker use the request’s product.
- `StrategyRepository._insert` accepts `"I"` and `"D"`.
- The new GTT entry and 15:14 exit use `I`.
- The one-time historical repair for run `160103` uses `D`, matching the executed trade.

Paper and existing ordinary-order paths may continue to use `D`.

### 4. Run states

- Add `gtt_active` and `closing` to the repository’s valid states.
- Include both in `list_unresolved`, because they need the 15:14 cleanup.
- `gtt_active` means “GTT submitted; Upstox manages entry/stop/target; cleanup pending”.
- `closing` means “15:14 cleanup has begun; do not submit another sell unless no exit order was submitted”.
- The old `reconciliation_required` state remains for genuine placement/cancel/exit uncertainty.

### 5. No daily order tracking

- `StrategyEngine._manage` must not call `OrderStatusPoller` for GTT runs.
- For `gtt_active`, `closing`, and GTT `exit_pending`, call the new `GttExecution.cleanup`.
- The old `GttExecution.manage` recovery path remains only for `reconciliation_required` runs, not the normal daily flow.
- The 30-second poller remains available for ordinary live orders, not the GTT strategy.

### 6. One-time repair of run 160103

Apply the operator-confirmed historical fills atomically:

- Entry order `261026000111597`, trades `370751` (500 @ 45.75) and `370752` (500 @ 45.80), average 45.775.
- Exit trades `753152` and `753153` (500 @ 45.95 each), no sell order ID.
- Set the run to `closed`, filled 1000, remaining 0, entry average 45.775.
- Record an auditable event. The repair is a one-off operation; it is not part of the daily flow.

### 7. Operational limits

- The worker must be running at 15:14. If it is down, the MIS position auto-squares off, but the GTT exit legs remain valid for 365 days and must be cancelled manually.
- An entry GTT that does not fill can remain active for up to one year. The 15:14 cleanup cancels it.
- The app does not report realized P&L or confirmed exit fills for the new flow; the broker statement remains the source of truth.

## Non-goals

- No continuous order-status polling for the GTT strategy.
- No child-order fill tracking or post-fill GTT modification.
- No full historical reconciliation service or 30-second recovery loop.
- No database schema change or new table.
- No frontend redesign.
- No live order submission during implementation or testing.

## Testing

- Product `I` is accepted and sent by the GTT place request; product `D` remains valid.
- GTT entry transitions to `gtt_active` and performs no polling.
- Cleanup before 15:14 does nothing.
- Cleanup at 15:14 with a flat position cancels the GTT and closes the run.
- Cleanup with a non-zero position cancels the GTT, submits one `I` SELL, records the order ID, and closes the run.
- Cleanup with an uncertain GTT cancel or sell submission becomes `reconciliation_required` and submits no second sell.
- Restart in `closing`/`exit_pending` with a recorded broker ID closes without another sell; without one, it fails closed.
- `gtt_active` and `closing` are unresolved; `closed` is not.
- The one-time repair is atomic, rejects a stale run or conflicting fills, and is idempotent.
- Full existing unittest suite.

## Acceptance criteria

- Run `160103` is `closed` with the four confirmed fills and zero remaining.
- The next session creates a new run and places one product `I` GTT on the ORB breakout.
- No 30-second order-status polling occurs for the GTT run.
- At 15:14 the GTT and working child orders are cancelled; a flat position closes the run, and an open position gets one bounded SELL.
- Genuine cancellation or exit uncertainty still blocks and alerts.
- No live order is submitted, modified, or cancelled during development or testing.
