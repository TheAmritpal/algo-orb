# Restricted stock-option expiry rollover

Implement in the current checkout, preserving prior documentation/test changes.

Policy: fresh entries select the nearest monthly expiry strictly after the next
NSE trading session. Broker exchange timings determine the next session, including
holidays and shifted expiry dates. This models Upstox's September/October 2026
restriction from the session before expiry, not a machine-readable guarantee of
all future broker policies. Existing exposure never rolls. Missing calendar data
blocks new entries but must not block position management. No live-mode or
protection verification flag changes.

- [x] Add failing tests for early rollover, expiry-day rollover, missing eligible
  contracts, holiday/weekend sessions and retained position management.
- [x] Add a snapshot entry-expiry boundary calculated/cached by MarketData;
  selection excludes contracts expiring on/before that boundary. Retry eligibility
  repeats that check for the original contract and never silently rolls a retry.
- [x] Update README/deployment instructions and run the complete mocked suite.
- [x] Apply authorized migration 0009 through the tunnel, verify schema/data and
  report production restart/build commands. Do not run integration fixtures in
  the production database.

Verification: 309 tests ran successfully, 11 opt-in MySQL tests skipped. No test
fixtures were run in production. Ruff checks passed for touched Python files
with the pre-existing FURB157 decimal-constructor style findings excluded;
`git diff --check` passed. Independent review identified weekend session
handling; it was corrected with a regression test.

Production: explicit upgrade 0008 -> 0009 completed through the existing tunnel.
Existing counts remained 6 runs, 2 orders, 1 GTT, 1 mutation and 2 GTT legs.
Primary key and new columns verified. Trading config stayed paper/live-disabled.
Private pre-migration backup is outside the checkout under the user's local
state backup directory. No worker/container restart or live activation performed.

Read-only broker checks: next session for 26 October is 27 October. Actual
available monthly expiries are 27 October, 23 November and 29 December 2026.
Both CE and PE selection for 26/27/28 October chooses 23 November, lot size 500.
These fixed-date checks used a synthetic spot solely to verify selection; they
were not an entry signal or an order request.
