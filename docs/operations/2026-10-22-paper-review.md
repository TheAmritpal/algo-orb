# Production paper review — 22 October 2026

Reviewed production MySQL through the existing localhost:3306 SSH tunnel using
read-only transactions. All times below are IST; persisted event times are UTC.
Database trading settings were `mode=paper`, `live_enabled=0`.

## 22 October: market-data failure, no execution

Run 17516 recorded “Waiting for fresh market data” at 09:15:01.408060.
At 15:00:10.950755 it changed to `skipped`, “Entry cutoff passed”. No opening
range, contract, signals, order requests or GTT records were persisted. Filled
and remaining quantities were zero. Heartbeat was still advancing at the review
(21:06:22 IST). A heartbeat therefore does not establish a working market feed.

The engine catches pre-signal exceptions and reports this generic market-data
message. The database does not retain the underlying exception, so the exact
failure requires production worker logs. Do not infer a token, quote-freshness
or broker-endpoint cause from this message alone.

## 21 October: planned-loss block

Run 11 recorded opening high 1234.30 and low 1208.00. The 13:45 candle close
was 1206.70, producing a PE signal. At 13:45:01 the worker selected the
27 October 1210 PE, instrument `NSE_FO|125856`, quantity 1000 (two 500-unit
lots), and blocked entry with `planned_loss_limit`. No order was persisted.

Before commit `88b57f1`, validation used `10 * quantity`, so 1000 units required
a 10000 planned-loss cap and would be blocked by 5000. That commit changed the
stop to five points and validation to `PREMIUM_STOP_DISTANCE * quantity`; it
was authored on 21 October at 16:33 IST, after the block. Current validation
uses strict `>`: exactly 5000 is allowed, 4999 blocks this quantity. This is a
consistent explanation of the historical block, not proof of deployed code or
the effective historical environment.

## Configuration reconciliation and remaining production verification

- Local private `.env.production`: `ORB_MAX_PLANNED_LOSS=5000`.
- Local development `.env`: `ORB_MAX_PLANNED_LOSS=10000`; this is a separate
  environment and was left unchanged.
- Tracked `deploy/production.env.example`: aligned from 10000 to 5000 to match
  the private production file and current five-point-stop calculation.
- Production database contains mode settings, not this risk cap or a historical
  code/configuration snapshot. Direct SSH authentication failed, so the actual
  server `.env.production`, running container environment and deployed source
  could not be verified or changed.

With authenticated server access, inspect only the named risk-cap variable in
the deployed file and worker environment; inspect the worker's stop constant
and entry validation code, and review 22 October worker logs with credential
redaction. Confirm the market-data failure before restarting or recreating a
worker. Environment-file edits require container recreation, not just restart.
Do not raise the cap to bypass a historical block or reset the skipped runs.
No production settings, run records or orders were changed during this review.
