# Upstox GTT execution for RELIANCE ORB

Status: approved by user on 2026-10-19, with target changed to +18 points.

## Intent

Move option target and stop-loss triggers to Upstox so exits do not wait for
the strategy worker's polling interval and network round trip. The user has
selected GTT despite the lack of Upstox sandbox support. This removes one
source of exit delay; it does not guarantee execution prices or fills.

Preserve RELIANCE's completed 15-minute candle breakout confirmation, nearest
monthly strictly ITM CE/PE selection, two lots, one entry attempt per session,
configured limits, 15:00 entry cutoff and 15:25 exit attempt (IST).

## Entry and initial protection

After the application confirms the underlying candle close, validate fresh
option quotes, funds, tick prices, limits and account ownership. Persist the
daily reservation and complete request before calling the official SDK's
OrderApiV3.place_gtt_order once.

Submit MULTIPLE with BUY ENTRY, TARGET and STOPLOSS on the selected option.
Use ENTRY IMMEDIATE at the validated ask, with initial TARGET ask + 18 and
STOPLOSS ask - 10. Both exit rules use IMMEDIATE as required by the API.
Set market_protection=-1 explicitly where supported by the installed SDK.
Do not use the underlying opening-range high/low as option trigger prices.

Important: Upstox documents IMMEDIATE entry as a limit order, not a market
order. This design therefore adopts GTT's documented limit entry semantics.
It does not promise to solve an unfilled entry by changing to MARKET.

Initial exit levels must be included with entry so protection is not first
created after the application notices a fill. Once actual trades establish
the average fill, request updated stop=average-10 and target=average+18 and
read back the GTT to confirm. Use tick-valid prices; if exact levels are
unrepresentable, do not round to increase planned risk. Cancel/reconcile
working legs and attempt a controlled exit if protection cannot be confirmed.
Never submit a second entry while attempting to update protection.

## Durable ownership and reconciliation

Add Alembic-managed storage for the GTT parent, leg states, generated exchange
order IDs, mutation intents and sanitized observations. Link them to the
existing strategy run and fill ledger. A GTT ID is not an exchange order ID.
Keep the existing ordinary order persistence available for historical runs
and controlled emergency/time exits.

Use a dedicated GTT broker adapter and orchestration path. Recover each run
using its persisted execution backend even when current settings differ.
The existing worker lock serializes mutations for an account. Persist each
create, modify and cancel intent before sending it; disable transport retries.

GTT acknowledgement and leg status do not prove a fill. Retrieve GTT details,
then reconcile generated exchange orders and trades using the existing SDK
order APIs. Validate instrument, side, quantity, account and parent linkage.
Apply each trade once. Unknown identity, missing evidence or conflicting
positions stops new submissions and requires reconciliation.

GTT creation has no documented client idempotency tag. If acknowledgement is
lost, retain the reservation and mark creation uncertain; never automatically
recreate a GTT or claim ownership of a similarly shaped account order.

## Partial fills, cancellation and timed exit

Retain the 30-second entry timeout. Inspect parent legs and exchange orders
before cancellation; cancellation acknowledgement is not final status.
Cancel any working exchange entry as well as remaining parent triggers, then
reconcile fills that raced with cancellation.

Do not assume GTT quantity can be reduced after opening: the API explicitly
restricts this. For a partial entry without verifiable correctly sized broker
protection, cancel/reconcile the parent and generated orders, then attempt to
flatten only the confirmed residual position. Do not recreate entry.

At 15:25, disable outstanding triggers, cancel working generated orders,
confirm terminal outcomes, reconcile trades and account position, then exit
only residual exposure using the existing bounded SELL path. If cancellation
cannot be confirmed, require reconciliation rather than risk a duplicate sell.
Repeat reconciliation across restarts until both exposure and outstanding
parent/child orders are resolved. Position zero alone does not close the run.

Never run the legacy polling-based TP/SL submission path concurrently with
GTT exit rules. Observe both exit legs and verify remaining sibling activity
instead of assuming mutual cancellation from an acknowledgement.

## Configuration and validation

Add an explicit execution-backend setting and select GTT for newly created
runs when configured. Keep historical ordinary-order runs recoverable.
Maintain existing live enablement, account and monetary-limit gates. Expose
the backend and reconciliation state through the existing strategy status
service without changing page layout.

Tests use mocks and deterministic simulation for SDK payloads, parent/child
mapping, actual fill reanchoring, partial fills, rejected modifications,
uncertain creation, sibling races, restarts, stale quotes, ownership loss,
timed exits and historical backend recovery. Migration tests cover new schema
and persisted round trips. Run the existing strategy and broker regressions.

GTT is not sandbox supported: report that limitation explicitly. Do not
substitute a live order for a sandbox test. Code integration does not enable
or start live trading; deployment and any real order verification are separate
operational actions. Do not claim broker validation from mocked tests.

## Alternatives considered

Ordinary protected MARKET entry with application-managed TP preserves the
current architecture but retains target polling delay; the user selected GTT.
Creating unrelated SELL triggers after a separate market entry would require
additional coordination and lacks the demonstrated multi-leg ownership flow.

## Sources and known limits

- https://upstox.com/developer/api-documentation/place-gtt-order/
- https://upstox.com/developer/api-documentation/modify-gtt-order/
- https://upstox.com/developer/api-documentation/get-gtt-order-details/
- https://upstox.com/developer/api-documentation/cancel-gtt-order/
- https://upstox.com/developer/api-documentation/sandbox/

The documentation does not establish safe partial-fill protection or atomic
cancellation of every already-generated exchange order. The conservative
paths above handle absent confirmation as uncertainty, not successful safety.
Actual post-entry modification and cancellation behavior remains unverified
with the broker because the sandbox excludes GTT.
