# Place-Once GTT with 15:14 Cleanup Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Place one product `I` GTT at the first-15-minute ORB breakout, stop all daily order tracking, run one 15:14 cleanup/exit, and repair run 160103 once so the next session is unblocked.

**Architecture:** Live GTT runs move from `entry_pending` to `gtt_active` after the GTT acknowledgement. The engine does not poll order status for these states. At 15:14 IST a single cleanup cancels the GTT and working children, reads the MIS position, and either closes the run (flat) or submits one bounded SELL and closes. Genuine cancellation/exit uncertainty becomes `reconciliation_required` and never submits a second sell. Paper and ordinary-order paths keep their existing behavior.

**Tech Stack:** Python 3.13, `uv`, official `upstox-python-sdk`, MySQL via PyMySQL, `unittest`.

**Spec:** `docs/superpowers/specs/2026-10-27-place-once-gtt-cleanup-design.md`

## Global Constraints

- No live order submission, modification, or cancellation during implementation or testing.
- No schema migration and no new table. New states are values only.
- Live GTT uses product `I`; the one-time repair of run 160103 uses product `D`.
- Never print, log, or persist access tokens, broker response bodies, or exception text.
- Diagnostics contain only fixed operation names, exception categories, numeric HTTP statuses, and validated `UDAPI` codes.
- Production apply requires the live worker stopped and its MySQL advisory lock held.
- Use `uv run python -m unittest ...`; do not use pytest.
- Do not run the production repair until the user confirms the worker is stopped.
- Worker must be running at 15:14 IST for the cleanup.

## Review Focus

- A GTT cancel or child cancel that is **uncertain** must block and never submit a sell.
- A restart in `closing`/`exit_pending` with **no recorded exit broker ID** must fail closed, not submit a second sell.
- A **flat** position at 15:14 must not create an exit order.
- An **open** position must submit exactly one product `I` sell and then close the run.
- The one-time repair must reject a **stale run**, **conflicting fills**, or a **non-flat** broker position.

---

### Task 1: Support product `I` and preserve `D`

**Files:**
- Modify: `app/strategies/models.py`
- Modify: `app/services/trading_broker.py`
- Modify: `app/services/gtt_broker.py`
- Modify: `app/strategies/gtt.py` (`_sync` child request only)
- Modify: `app/repositories/strategy.py` (`_insert`)
- Modify: `app/strategies/engine.py` (`_request` product selection)
- Test: `tests/test_trading_broker.py`, `tests/test_gtt_broker.py`, `tests/test_gtt_repository.py`

**Interfaces:**
- `OrderRequest` accepts `product in {"I", "D"}`.
- `Broker.submit` uses `request.product`; `Broker.reconcile` compares `raw.product == req.product`.
- `GttBroker.place` uses `req.product`; `GttBroker.snapshot` and `child_order` compare against `req.product`.
- `StrategyRepository._insert` accepts `request.product in {"I", "D"}`.
- `StrategyEngine._request(..., product="D")` accepts an override; the live GTT entry request uses `I`.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_gtt_broker.py`:

```python
def test_place_uses_request_product_i(self):
    request = OrderRequest("gtttest", "NSE_FO|123", "I", "BUY", "LIMIT", 500, D(100))
    record = GttRecord(1, request, D(90), D(118), datetime.now(UTC), "GTT-123")
    self.api.place_gtt_order.return_value = N(
        status="success", data=N(gtt_order_ids=["GTT-123"])
    )
    self.broker.place(record)
    self.assertEqual(
        self.api.place_gtt_order.call_args.kwargs["body"].product, "I"
    )

def test_snapshot_accepts_matching_product_i(self):
    # same raw fixture as test_snapshot_validates_parent_and_all_legs,
    # with product="I" and a record whose request product is "I"
    ...
```

Add to `tests/test_trading_broker.py`:

```python
def test_submit_uses_request_product_i(self):
    request = replace(self.request, product="I")
    self.api.place_order.return_value = N(status="success", data=N(order_id="o1"))
    self.broker.submit(request)
    self.assertEqual(
        self.api.place_order.call_args.args[0].product, "I"
    )
```

Add to `tests/test_gtt_repository.py` (or the repository test module): a request with product `I` can be inserted; a product other than `I`/`D` is rejected.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run python -m unittest tests.test_trading_broker tests.test_gtt_broker tests.test_gtt_repository -v`

Expected: FAIL because `OrderRequest` rejects `I` and the adapters hard-code `D`.

- [ ] **Step 3: Implement product support**

- `OrderRequest.__post_init__`: change `self.product != "D"` to `self.product not in {"I", "D"}`.
- `Broker.submit`: `product=request.product`.
- `Broker.reconcile`: identity tuple `(req.instrument, req.product, req.side, req.quantity)`.
- `GttBroker.place`: `product=req.product`.
- `GttBroker.snapshot`: identity tuple uses `req.product`.
- `GttBroker.child_order`: tuple uses `record.request.product`.
- `GttExecution._sync` child `OrderRequest(... product=record.request.product ...)`.
- `StrategyRepository._insert`: `request.product not in {"I", "D"}` rejects.
- `StrategyEngine._request` gains `product="D"` and passes it to `OrderRequest`. The live GTT entry call at the breakout passes `product="I"` when `self.settings.mode == "live" and self.settings.execution_backend == "gtt"`; otherwise `"D"`.
- Leave paper and ordinary paths on `D`.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run python -m unittest tests.test_trading_broker tests.test_gtt_broker tests.test_gtt_repository tests.test_strategy_engine tests.test_gtt_engine -v`

Expected: PASS; existing D behavior unchanged.

- [ ] **Step 5: Commit**

```bash
git add app/strategies/models.py app/services/trading_broker.py app/services/gtt_broker.py app/strategies/gtt.py app/repositories/strategy.py app/strategies/engine.py tests/test_trading_broker.py tests/test_gtt_broker.py tests/test_gtt_repository.py
git commit -m "feat: support intraday product for live GTT orders"
```

---

### Task 2: Place once and mark the live run `gtt_active`

**Files:**
- Modify: `app/strategies/gtt.py` (`enter`)
- Modify: `app/repositories/strategy.py` (`STATES`, `list_unresolved`)
- Modify: `app/strategies/engine.py` (`_manage`)
- Modify: `app/services/strategy_status.py`
- Modify: `tests/strategy_helpers.py`
- Test: `tests/test_gtt_engine.py`, `tests/test_strategy_status.py`, `tests/test_order_status.py`

**Interfaces:**
- `GttExecution.enter` transitions a live run to `gtt_active` after `acknowledge`; paper mode keeps its existing `entry_pending`/`manage` behavior.
- `STATES` includes `gtt_active` and `closing`.
- `list_unresolved` includes `gtt_active` and `closing`.
- `StrategyEngine._manage` skips `OrderStatusPoller` for live GTT `gtt_active`/`closing`/`exit_pending` and calls `GttExecution.cleanup` instead.
- New-state messages in `strategy_status`.

- [ ] **Step 1: Write the failing tests**

In `tests/test_gtt_engine.py`, add a live-mode case:

```python
def test_live_gtt_enter_marks_gtt_active_and_does_not_sync(self):
    self.engine.settings.mode = "live"  # or construct a live TradingSettings
    self.enter()
    run = self.repo.get_run(self.run.id)
    self.assertEqual(run.state, "gtt_active")
    self.assertFalse(any(i.purpose == "entry" for i in self.repo.intents.values()))
    self.adapter.snapshot.assert_not_called()
    self.adapter.child_order.assert_not_called()
```

In `tests/test_order_status.py`, add:

```python
def test_live_gtt_active_run_is_not_polled(self):
    # make a live gtt run in gtt_active, then engine.tick(at())
    # assert self.orders.get_order_status.call_count == 0
```

In `tests/test_strategy_status.py`, add assertions for `gtt_active` and `closing` messages.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run python -m unittest tests.test_gtt_engine tests.test_order_status tests.test_strategy_status -v`

Expected: FAIL because live `enter` leaves the run in `entry_pending` and the engine still polls.

- [ ] **Step 3: Implement the state and routing**

- `StrategyRepository.STATES`: add `"gtt_active"` and `"closing"`.
- `list_unresolved`: include `'gtt_active','closing'` in the state list.
- `tests/strategy_helpers.MemoryRepository.list_unresolved`: include the same states.
- `GttExecution.enter`: after `self.repository.acknowledge(run.id, broker_id)`, if `run.key.mode == "live"`:
  ```python
  current = engine.repository.get_run(run.id)
  return engine._state(
      current,
      "gtt_active",
      message="GTT placed; Upstox manages exits until 15:14 cleanup",
  )
  ```
  Paper returns the run as today.
- `StrategyEngine._manage`: compute
  ```python
  new_gtt = (
      run.key.mode == "live"
      and run.execution_backend == "gtt"
      and run.state in {"gtt_active", "closing", "exit_pending"}
  )
  if run.key.mode == "live" and not new_gtt:
      ... existing poller block ...
  if run.execution_backend == "gtt":
      if new_gtt:
          return self.gtt.cleanup(run, now)
      if run.state == "reconciliation_required" and not any(
          i.purpose == "entry" for i in self._intents(run)
      ):
          return run  # parked for manual attention; do not re-enter legacy sync
      return self.gtt.manage(run, now)
  ```
- `strategy_status`: add messages for `gtt_active` and `closing`.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run python -m unittest tests.test_gtt_engine tests.test_order_status tests.test_strategy_status tests.test_strategy_engine -v`

Expected: PASS; paper GTT tests unchanged.

- [ ] **Step 5: Commit**

```bash
git add app/strategies/gtt.py app/repositories/strategy.py app/strategies/engine.py app/services/strategy_status.py tests/strategy_helpers.py tests/test_gtt_engine.py tests/test_order_status.py tests/test_strategy_status.py
git commit -m "feat: place live GTT once and mark it active"
```

---

### Task 3: 15:14 cleanup and single exit

**Files:**
- Modify: `app/services/gtt_broker.py` (`status`)
- Modify: `app/strategies/gtt.py` (`cleanup`, `_resume_cleanup`)
- Modify: `app/repositories/strategy.py` (`record_submission`)
- Modify: `app/strategies/engine.py` only if a helper is needed
- Test: `tests/test_gtt_engine.py`, `tests/test_gtt_broker.py`

**Interfaces:**
- `GttBroker.status(record) -> GttSnapshot | None` returns `None` when the GTT is no longer found; multiple results still raise.
- `GttBroker.snapshot(record)` calls `status` and raises when `None`, preserving existing callers.
- `GttExecution.cleanup(run, now) -> StrategyRun`.
- `StrategyRepository.record_submission(intent_id, broker_id) -> None`.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_gtt_broker.py`:

```python
def test_status_returns_none_for_missing_gtt(self):
    self.api.get_gtt_order_details.return_value = N(status="success", data=[])
    self.assertIsNone(self.broker.status(self.record))

def test_status_accepts_product_i_snapshot(self):
    # product="I" raw and record; returns a snapshot
    ...
```

Add to `tests/test_gtt_engine.py` a live cleanup suite:

```python
def test_cleanup_before_1514_does_nothing(self):
    # live gtt_active run, now=15:13
    self.execution.cleanup(self.repo.get_run(self.run.id), at(15, 13))
    self.adapter.cancel.assert_not_called()

def test_cleanup_flat_cancels_parent_and_children_then_closes(self):
    # gtt_active, snapshot with an OPEN entry child, positions empty
    # cleanup(at(15,14))
    # assert parent cancel, child cancel, state closed, no sell

def test_cleanup_open_position_submits_one_i_sell_and_closes(self):
    # gtt_active, snapshot terminal, positions +500 product I
    # cleanup(at(15,14))
    # assert one submit with product I, exit intent broker ID recorded, state closed

def test_cleanup_cancel_uncertainty_blocks_without_sell(self):
    # adapter.cancel raises TimeoutError
    # assert reconciliation_required, no submit

def test_cleanup_submit_uncertainty_blocks_and_never_resubmits(self):
    # live_broker.submit raises TimeoutError
    # first cleanup -> reconciliation_required
    # second cleanup -> no second submit

def test_cleanup_restart_with_recorded_exit_closes_without_second_sell(self):
    # state closing, exit intent broker_id recorded
    # cleanup -> closed, submit not called

def test_cleanup_restart_without_broker_id_fails_closed(self):
    # state closing, exit intent no broker_id
    # cleanup -> reconciliation_required, submit not called
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run python -m unittest tests.test_gtt_broker tests.test_gtt_engine -v`

Expected: FAIL because `status`, `cleanup`, and `record_submission` do not exist.

- [ ] **Step 3: Implement the cleanup**

- `GttBroker.status`: call `sdk_data(get_gtt_order_details)`. Return `None` for an empty list; raise for non-list or more than one. Parse the raw into a `GttSnapshot` using the existing validation. `snapshot` calls `status` and raises `TradingUnavailable` when `None`.
- `GttExecution.cleanup`:
  - Return immediately before `time(15, 14)` IST or when the state is `closed`.
  - For `closing`/`exit_pending`, call `_resume_cleanup`.
  - For `gtt_active`:
    1. get the `GttRecord`; fail if no `broker_id`;
    2. `self.broker.status(record)`; treat `BrokerOrderNotFound` as `None`;
    3. if a snapshot exists, cancel the parent; cancel every leg with an `order_id` and status in `{"OPEN","TRIGGERED","PENDING"}`; treat `BrokerOrderNotFound` as already terminal; any other error → `_fail` and no sell;
    4. re-read `status` once; if a snapshot remains with any non-terminal leg → `_fail` and no sell;
    5. read positions for the contract and product `I`;
    6. flat → `_state(run, "closed", message="Day-end cleanup: flat")`;
    7. negative → `_fail`;
    8. non-zero → compute a bounded sell quote, transition to `closing` with `filled_quantity=actual` and `remaining_quantity=actual`, create an exit intent with product `I`, submit once, `record_submission`, append `cleanup_exit_submitted`, and transition to `closed` with `remaining_quantity=0` and “Exit submitted at 15:14; MIS auto-square-off backstop”.
  - `_resume_cleanup`: if an exit intent has a `broker_id`, close the run without another submit; if not, `_fail` with “Exit submission outcome unknown; reconciliation required”.
  - Never submit a second sell after an uncertain submit.
- `StrategyRepository.record_submission`: update `strategy_orders.broker_id` and keep `status='unknown'`; do not change the run.
- Add `cleanup_exit_submitted`/`position_checked` events with allowlisted fields.
- Import `bounded_sell`, `replace`, `BrokerOrderNotFound`, and `time(15, 14)`.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run python -m unittest tests.test_gtt_broker tests.test_gtt_engine tests.test_gtt_repository -v`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/services/gtt_broker.py app/strategies/gtt.py app/repositories/strategy.py tests/test_gtt_broker.py tests/test_gtt_engine.py
git commit -m "feat: add single 15:14 GTT cleanup and exit"
```

---

### Task 4: One-time atomic repair of run 160103

**Files:**
- Modify: `app/repositories/strategy.py` (`_apply_order` refactor, `repair_manual_exit`)
- Create: `app/cli/repair_manual_exit.py`
- Test: `tests/test_manual_exit_repair.py`, `tests/test_repair_manual_exit_cli.py`

**Interfaces:**
- `StrategyRepository._apply_order(c, intent_id, order) -> StrategyRun` is the transaction-body extraction of `apply_order`.
- `StrategyRepository.repair_manual_exit(run, entry_order, exit_request, exit_order, *, apply=False) -> dict`.
- CLI `reconcile_manual_exit(manifest, *, apply=False) -> dict`; `main(argv=None) -> int`.

- [ ] **Step 1: Write the failing tests**

Repository tests:

```python
def test_dry_run_repair_projects_closed_without_writes(self):
    result = self.repo.repair_manual_exit(...)
    self.assertEqual(
        (result["state"], result["filled_quantity"], result["remaining_quantity"]),
        ("closed", 1000, 0),
    )
    self.assertFalse(result["applied"])
    # no INSERT/UPDATE/DELETE

def test_apply_repair_writes_entry_exit_and_closed_state_atomically(self):
    # assert entry fills 370751/370752, exit fills 753152/753153,
    # exit intent broker_id is the synthetic histfill ID,
    # run state closed, entry_average 45.775, one commit

def test_stale_run_or_conflicting_fill_refuses(self):
    # version mismatch and an existing fill with different price -> ValueError, no commit

def test_identical_replay_is_idempotent(self):
    # already closed with matching fills -> no writes
```

CLI tests:

```python
def test_dry_run_takes_no_worker_lock(self): ...
def test_apply_refuses_when_worker_is_running(self): ...
def test_nonflat_position_refuses(self): ...
def test_resources_close_on_error(self): ...
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run python -m unittest tests.test_manual_exit_repair tests.test_repair_manual_exit_cli -v`

Expected: FAIL because the method and CLI do not exist.

- [ ] **Step 3: Implement the atomic repair and CLI**

- Refactor `apply_order`:
  ```python
  def apply_order(self, intent_id, order):
      with transaction() as c:
          return self._apply_order(c, intent_id, order)

  def _apply_order(self, c, intent_id, order):
      ... existing body, returning run ...
  ```
- `repair_manual_exit`:
  - Validate live, product `D`, state `reconciliation_required`, expected version, entry intent broker `261026000111597`, entry order tag/quantity/broker ID, exit request tag/quantity/side/product `D`, and the fills’ IDs/quantities.
  - Dry run: return the projected closed summary; no writes.
  - Apply: one transaction; `_read(..., True)`; re-check version; `_apply_order` entry; `_insert` exit with the `hist…` tag; `_apply_order` exit; set state `closed`, message `"Manual exit 2026-10-26 reconciled"`; `_save`; append `historical_reconciled` with the four trade IDs; commit.
  - Idempotent replay: if already closed and the fills match, return `applied=False`.
- CLI constants for this incident:
  - entry tag from intent `7`; entry broker `261026000111597`; entry fills `370751` 500 @ 45.75, `370752` 500 @ 45.80.
  - exit request tag `hist…`, product `D`, side `SELL`, quantity 1000, price 45.95; exit broker/fill order `histfill…`; fills `753152` 500 @ 45.95, `753153` 500 @ 45.95.
  - Fetch the run, verify the account, load the live token, verify the profile, check `Broker.positions()` is flat for `NSE_FO|144161`/`D`, acquire `WorkerLock` only for `--apply`, re-read the run, call `repair_manual_exit`.
  - Print JSON; on error print `failure_details`-style sanitized metadata; close resources in `finally`.
  - No broker mutations.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run python -m unittest tests.test_manual_exit_repair tests.test_repair_manual_exit_cli -v`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/repositories/strategy.py app/cli/repair_manual_exit.py tests/test_manual_exit_repair.py tests/test_repair_manual_exit_cli.py
git commit -m "feat: add atomic manual-exit repair for run 160103"
```

---

### Task 5: Documentation and full verification

**Files:**
- Modify: `README.md`
- Modify: `deploy/README.md`
- Test: full suite

- [ ] **Step 1: Document the new flow**

In `README.md`:
- Live GTT uses product `I`; entry −5, target +8, no post-fill modification.
- Run states `gtt_active` → `closing` → `closed`.
- No 30-second polling for live GTT.
- 15:14 cleanup: cancel GTT/children, flat → closed, open → one bounded sell, uncertainty → `reconciliation_required`.
- Worker must run at 15:14; MIS auto-square-off is the backstop; orphan GTT legs require manual cancellation if the worker is down.
- One-time repair command for 160103.

In `deploy/README.md`:
- Stop worker, run the repair dry run/apply, verify, deploy the new code, restart after the session close.
- Note that tomorrow’s run is blocked until 160103 is closed.

- [ ] **Step 2: Run the full suite**

Run: `uv run python -m unittest discover -v`

Expected: all tests pass; MySQL-gated tests skipped unless `ORB_MYSQL_TESTS=1`.

- [ ] **Step 3: Commit**

```bash
git add README.md deploy/README.md
git commit -m "docs: document place-once GTT and 15:14 cleanup"
```

---

### Task 6: Production repair and restart

**Files:** none (production data operation).

- [ ] **Step 1: Ask the user to stop the worker**

On the production host:

```bash
docker compose stop worker
docker compose ps
```

Do not proceed until the user confirms.

- [ ] **Step 2: Dry run the repair**

```bash
PYTHONPATH=. uv run python -m app.cli.repair_manual_exit
```

Expected: run 160103 projected `closed`, filled 1000, remaining 0, entry average 45.775.

- [ ] **Step 3: Apply the repair**

```bash
PYTHONPATH=. uv run python -m app.cli.repair_manual_exit --apply
```

Expected: `applied=true` and the same figures.

- [ ] **Step 4: Verify the database**

```sql
SELECT id, state, filled_quantity, remaining_quantity, entry_average, message
FROM strategy_runs WHERE id = 160103;

SELECT id, purpose, broker_id, status, tag
FROM strategy_orders WHERE run_id = 160103 ORDER BY id;

SELECT trade_id, order_id, quantity, price
FROM strategy_fills f JOIN strategy_orders o ON o.id = f.intent_id
WHERE o.run_id = 160103 ORDER BY trade_id;
```

Expected: `closed`, 1000, 0, 45.775; entry complete; exit complete with no real broker ID; four fills.

- [ ] **Step 5: Deploy the new code**

The production worker still runs the old code until the image is rebuilt. The DB repair alone unblocks tomorrow but does not enable the place-once flow. The user runs the deployment from `deploy/README.md` outside the session, then restarts the worker after 15:30 IST.

- [ ] **Step 6: Restart after the session close**

After 15:30 IST:

```bash
docker compose up -d --no-deps --force-recreate worker
docker compose logs --tail 50 worker
```

Confirm the heartbeat; today’s run is `skipped`; tomorrow a new run is created and can place its product `I` GTT.
