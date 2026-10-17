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

## Final review and verification (2026-10-17)

The independent reviewer identified the partial-fill risk path but hit an external usage limit before returning its full report. The author completed the whole-branch review; this is weaker than a completed independent review and must not be reported as independent approval.

Eleven new regression tests were observed failing before fixes. They cover off-tick partial averages, pending entries after partial stop/target exits, triggered paper stops across gaps/restarts, stale paper fills, live-mode labels on errors, bounded MySQL sockets, quote receipt timing, heartbeat preservation, malformed broker positions, and retryable pre-entry market-read failures. Each now passes.

Final results: 122 tests passed; changed Python files passed Ruff formatting/checks; offline MySQL migration SQL generated successfully; git diff whitespace checks passed. No deferred minor findings. Real-server MySQL integration and broker protective-stop acceptance were not performed. Live trading remains disabled, and no real order, deployment, or live migration was run.

Additional decisions:

- Complete review as the author after the independent reviewer's usage-limit failure. Cost if wrong: author blind spots remain before merge.
- Bound shared MySQL connection/read/write operations to five seconds. Cost if wrong: legitimately slow queries fail and need operator attention.

## Execution ledger


```text
# SDD ledger — plan: docs/superpowers/plans/2026-10-16-reliance-orb.md
Native execution approved. Baseline: 61 tests passed.
Pre-flight: 1→2 domain records consumed by repository; signatures consistent.
Pre-flight: 1/2→3 SDK adapters consume domain types and pinned-token access; consistent.
Pre-flight: 1/2/3→4 engine requires repository readback of intents; add get_run/list_intents methods, no strategy behavior change.
Pre-flight: 1/2/3/4→5 worker constructs adapters; consistent.
Pre-flight: 2→6 dashboard projects latest run; consistent.
Pre-flight: 1–6→7 integration uses same interfaces; consistent.
Ruling: Adopt explicit get_run/list_intents repository methods in addition to the planned interface — engine recovery needs durable readback — cost if wrong: internal interface revision.
Task 1: complete (commits 661f483..069c063, tests: uv run --no-sync python -m unittest tests.test_strategy_rules tests.test_trading_settings -q → OK)
Task 2: complete (commits 069c063..85843c3, tests: uv run --no-sync python -m unittest tests.test_strategy_repository tests.test_worker_lock tests.test_migrations -q → OK)
Ruling: Add an explicit protective-stop verification deployment flag, default false — sandbox acceptance cannot be established by mocked SDK tests — cost if wrong: live entries stay disabled until deployment verifies support.
Ruling: Normalize broker tick metadata from paise to rupees and add shared SDK rate gating — published option contract examples expose tick_size=5 — cost if wrong: contracts are rejected by tick validation, requiring metadata correction before live activation.
Ruling: PaperBroker.restore and MarketData.quote supplement the planned interfaces for restart hydration and actionable depth — cost if wrong: internal adapter API adjustment.
Task 3: complete (commits 85843c3..a707ce8, tests: uv run --no-sync python -m unittest tests.test_trading_market tests.test_trading_broker tests.test_paper_broker tests.test_tokens -q → OK)
Task 4: complete (commits a707ce8..0982a09, tests: uv run --no-sync python -m unittest tests.test_strategy_engine tests.test_trading_broker tests.test_paper_broker tests.test_strategy_repository -q → OK)
Task 5: complete (commits 0982a09..ddaa014, tests: uv run --no-sync python -m unittest tests.test_strategy_worker tests.test_strategy_engine tests.test_trading_settings -q → OK)
Task 6: complete (commits ddaa014..686eaeb, tests: uv run --no-sync python -m unittest tests.test_strategy_status tests.test_upstox_status tests.test_auth tests.test_upstox_auth -q → OK)
Ruling: Recheck injected execution clock after durable reservation, before entry RPC — snapshot time can become stale during broker validation — cost if wrong: a delayed signal consumes the allowance without placing an order.
Task 7: complete (commits 686eaeb..f774fc1, tests: uv run --no-sync python -m unittest discover -q → OK)
Final review: fresh reviewer reached an external usage limit before completing its report; completed whole-branch review by author with that limitation disclosed.
Final review findings accepted: off-tick partial average blocks closure; pending entry survives partial exit/target; paper stop loses trigger on gap rebound; stale paper quote can fill; error status can mislabel live as paper; unbounded MySQL socket operations can stall worker.
Final: Ruling: Complete review as author after independent reviewer usage-limit failure — no further independent capacity available — cost if wrong: remaining author blind spots before merge.
Final: Ruling: Bound shared MySQL connection/read/write operations to five seconds — an unresponsive database must not hang strategy scheduling indefinitely — cost if wrong: legitimately slow database operations fail and need operator attention.
Final review findings accepted: fresh quotes newer than the polling timestamp were rejected; stale run snapshots overwrote the dedicated heartbeat update. Both have failing regression tests before fixes.
Final review findings accepted: malformed broker position quantity must fail validation; transient market reads must retain retry eligibility and reach bounded worker backoff.
Final: fixed off-tick partial averages — test_off_tick_partial_average_cancels_remainder_and_closes_confirmed_units RED→GREEN, suite 122/122.
Final: fixed pending entry surviving partial stop/target — test_partial_stop_fill_cancels_entry_before_wait_window_ends and test_partial_target_cancels_entry_and_exits_without_waiting_30_seconds RED→GREEN, suite 122/122.
Final: fixed paper trigger persistence and stale fills — test_triggered_paper_stop_remains_executable_after_gap_rebound_and_restart and test_paper_does_not_fill_on_stale_quote RED→GREEN, suite 122/122.
Final: fixed live-mode status on DB failure — test_database_failure_keeps_live_mode_label RED→GREEN, suite 122/122.
Final: fixed unbounded DB sockets — test_mysql_socket_operations_have_bounded_timeouts RED→GREEN, suite 122/122.
Final: fixed quote receipt-time validation — test_quote_received_after_tick_start_is_checked_at_receive_time RED→GREEN, suite 122/122.
Final: fixed heartbeat clobbering — test_run_update_cannot_overwrite_a_newer_worker_heartbeat RED→GREEN, suite 122/122.
Final: fixed invalid positions — test_malformed_broker_position_quantity_is_rejected RED→GREEN, suite 122/122.
Final: fixed transient pre-entry read handling — test_transient_market_read_failure_can_retry_without_consuming_day RED→GREEN, suite 122/122.
Final review: no deferred minor findings. Live broker acceptance and real-server MySQL integration remain explicitly unexecuted deployment checks; no real orders or migrations were run.
```
