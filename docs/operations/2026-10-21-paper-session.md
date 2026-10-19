# Paper rehearsal — 21 October 2026

Select Paper mode and disable live trading in the dashboard before starting the worker.
These settings are stored in MySQL; environment flags no longer control execution mode. The existing GTT execution backend uses PaperBroker
and PaperGttBroker in this mode. Market data uses the pinned Upstox account;
order placement, fills and GTT exits are simulated. Protection verification
remains disabled for live orders.

## Run on 21 October (IST)

1. Reconnect Upstox for a valid trading-day token and confirm MySQL is available.
2. Start the worker before 09:15 IST from the project root:

   ```bash
   uv run python -m app.cli.run_strategy
   ```

   The explicit environment settings also override any inherited live settings.
   Restart the web application to load the updated local configuration.
3. Keep the worker running through 15:25 IST and until the paper session has
   reconciled. Inspect `/strategy/history` for the paper account session,
   candle decisions, selected contract, entry/block reasons, fills, GTT legs
   and final position state. No signal is a valid outcome; it does not verify
   the entry/exit path.
4. Stop the paper worker after reviewing the final state. Do not leave it
   running for another session if the intention is a one-day rehearsal.

This change configures execution mode; it does not install a scheduler or start
the worker. The existing worker continues across exchange days until stopped.

## Review before 22 October

Paper mode stays enabled until explicitly changed. There is no automatic live
switch at midnight. Review the 21 October results and resolve any stale
heartbeat, reconciliation error or unresolved paper position before proceeding.
Paper success does not establish actual broker GTT eligibility, execution,
slippage or profitability.

Before live trading, confirm sufficient funding, a valid token, registered
deployment IP, actual account-specific protection evidence, worker supervision
and an operator exit plan. Preserve `ORB_PROTECTIVE_STOP_VERIFIED=false` until
that evidence exists. See `2026-10-19-readiness.md` for the outstanding live
readiness requirements. A future live worker must be started separately after
the paper worker is stopped and live configuration has been explicitly reviewed.
