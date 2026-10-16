# RELIANCE execution verification

Branch: feat/reliance-orb, based on 661f483. Native execution selected by user.

## Checks before independent review

- Existing baseline: 61 tests passed.
- Full suite: `uv run --no-sync python -m unittest discover -q` — 111 tests passed.
- Ruff format and check on changed Python files — passed.
- `uv run --no-sync alembic upgrade head --sql` — generated all four 0003 strategy tables offline, including daily/trade uniqueness and foreign keys.
- `git diff --check` — passed.
- SDK boundary checked against installed official upstox-python-sdk 2.28.0 and local reference docs.
- Strategy/SDK/MySQL tests use mocks, deterministic paper execution and actual SDK response models; no live orders, live worker, broker sandbox mutation or live database migration was run.
- MySQL connection/migration integration against a real server and broker acceptance of protective stops remain deployment checks. This checkout's existing credential file was not changed.

## Implementation decisions

- Supplement the repository with get_run/list_intents methods so recovery can read durable intent state. Cost if wrong: internal API revision.
- Add an explicit protective-stop verification flag, false by default. Mocked payload checks cannot establish broker/account acceptance. Cost if wrong: live entry remains blocked pending sandbox verification.
- Convert broker metadata ticks from paise to rupees and throttle SDK requests per worker. Cost if wrong: tick validation blocks the affected contract until metadata mapping is corrected.
- Supplement PaperBroker with restore and MarketData with quote to reconstruct paper state and obtain actionable depth. Cost if wrong: internal adapter API revision.
- Recheck execution time after durable entry reservation. Cost if wrong: a delayed signal consumes the day's allowance without placing an order.

Independent review and any verified fixes will be recorded below.
