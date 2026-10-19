# Deployment checks — 19 October 2026

Live trading remains blocked pending actual protection verification and funding.
No strategy worker was started and no live order was placed, modified or cancelled.

## Applied changes

- Migrated the configured MySQL database from 0004 through 0005 to 0006.
- Added observed candle decisions and revision evidence to `strategy_signals`.
- Added authenticated `/strategy/history`, scoped to the configured broker app,
  pinned account and selected paper/live mode. It displays session state,
  decisions, selected contracts, entry block/defer reasons, orders, fills,
  GTT parent/leg observations and pending GTT mutations.
- Preserved existing records; did not invent past signals or trading history.
- Added active-account, NFO, D, LIMIT and SL profile checks to the live broker's
  protection capability gate. The explicit protection verification flag is
  still required and has not been enabled.

## Local configuration

The verified account is pinned in `.env`; identifiers and credentials are omitted
from this report. Settings are prepared for live mode, with live enablement true,
GTT execution, two lots, 2-second polling and 5-second quote freshness.

| Setting | Value |
| --- | --- |
| Unit premium cap | Explicitly disabled (`none`), as requested |
| Total premium cap | Explicitly disabled (`none`), as requested |
| Planned premium stop / target | −10 / +18 points per unit |
| Total planned-loss cap | ₹10,000 |
| Maximum bid–ask spread | ₹1 per unit |
| Ordinary sell-limit slippage | ₹1 per unit |
| Protection verification | **false — entries blocked** |

The broker's current monthly RELIANCE lot size is 500 units. Two lots are
1,000 units, making the ten-point planned stop ₹10,000 before fees and execution
losses. The spread/slippage settings are initial execution constraints. GTT
market protection remains the existing automatic setting; the local rupee sell
slippage setting does not cap GTT execution slippage or guarantee the stop loss.

## Verification evidence

- Full suite with `ORB_MYSQL_TESTS=1`: **199 tests passed, none skipped**.
- Five MySQL integration tests cover token/environment isolation, audit
  blocking, decision revisions/deduplication, account/mode history scoping,
  GTT persistence/recovery journals, child mapping and unique trade fills.
- Ruff checks and whitespace checks passed.
- Independent code review completed; its two history findings were fixed and
  regression tested.
- Final MySQL readback confirmed revision 0006 and zero strategy runs, signals,
  orders, fills, GTT parents and GTT mutations after fixture cleanup.

## Broker evidence and remaining work

Read-only SDK checks verified the saved token matches the pinned active account,
NFO/D/LIMIT/SL permissions, registered primary IP, GTT read access and RELIANCE
contract metadata. The account reports no positive available margin, zero open
positions and zero orders. These checks do not prove that the eventual deployment
host uses the registered IP or that live GTT placement and protection work.

Fund the intended account and verify account-specific protection with broker
evidence before setting `ORB_PROTECTIVE_STOP_VERIFIED=true`. Any live order test
needs explicit authorization of its instrument, quantity, price and exit plan.
Start/restart the web application to load the new route and current settings.
Keep the separate worker supervised through its 15:25 IST exit and reconciliation;
no worker startup or future trading schedule has been configured here.
