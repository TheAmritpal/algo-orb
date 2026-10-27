# Order Reconciliation Recovery Implementation Plan

> **Superseded by** `2026-10-27-place-once-gtt-cleanup.md`. The project moved to
> a simpler place-once GTT flow with a single 15:14 cleanup.

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close live run 160103 with its confirmed historical fills, stop expired prior-day orders from blocking the next session, and keep unresolved exposure or active GTTs blocking.

**Architecture:** The poller distinguishes an expired broker order record from other failures and returns an explicit outcome. The engine parks a prior-day run as `reconciliation_expired` only when the broker position is flat and the stored GTT is terminal; otherwise it keeps `reconciliation_required`. A dry-run-first operator CLI validates an explicit manifest against the authenticated historical-trades API before an atomic repository repair.

**Tech Stack:** Python 3.13, `uv`, official `upstox-python-sdk`, MySQL via PyMySQL, `unittest`.

**Spec:** `docs/superpowers/specs/2026-10-27-order-reconciliation-recovery-design.md`

## Global Constraints

- No live order submission, modification, or cancellation during implementation or repair.
- No schema migration and no new table. `reconciliation_expired` is a new state value only.
- Never print, log, or persist access tokens, broker response bodies, or exception text.
- Diagnostics contain only fixed operation names, exception categories, numeric HTTP statuses, and validated `UDAPI` codes.
- Production apply requires the live worker to be stopped and its MySQL advisory lock held.
- Use `uv run python -m unittest ...` for tests. Do not use pytest.
- Do not run the production `--apply` until the user confirms the worker is stopped.
- Today is 2026-10-27 IST. Repair run `160103` for exchange date 2026-10-26.

## Review Focus

- A `404` for a **current-day** order must remain unverified, not `expired`: the order may still appear.
- A prior-day run with **open exposure** must keep blocking; it must never become `reconciliation_expired`.
- A prior-day run with an **active GTT leg** must keep blocking; parking it could allow a later sell.
- A **duplicate or conflicting trade ID** must reject the entire repair, including the dry run.
- `--apply` must refuse before any write when the **worker lock** is held or the position is not flat.

---

### Task 1: Sanitized broker failure metadata

**Files:**
- Modify: `app/services/trading_market.py`
- Test: `tests/test_trading_broker.py`

**Interfaces:**
- Produces: `TradingUnavailable(message, *, http_status=None, broker_code=None)` with attributes `http_status: int | None` and `broker_code: str | None`.
- Produces: `BrokerOrderNotFound(TradingUnavailable)`.
- Produces: `sdk_data(method, *args, **kwargs)` raises `BrokerOrderNotFound` for HTTP `404` with broker code `UDAPI100010`, otherwise `TradingUnavailable` carrying sanitized metadata.
- Consumes: no new task interfaces.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_trading_broker.py`:

```python
import json
from upstox_client.rest import ApiException
from app.services.trading_broker import Broker, TradingUnavailable
from app.services.trading_market import BrokerOrderNotFound

def test_order_not_found_preserves_only_sanitized_metadata(self):
    body = json.dumps(
        {"errors": [{"errorCode": "UDAPI100010", "message": "Order not found"}]}
    )
    self.api.get_order_status.side_effect = ApiException(status=404, body=body)
    intent = OrderIntent(1, 1, "entry", self.request, at(), broker_id="o1")
    with self.assertRaises(BrokerOrderNotFound) as raised:
        self.broker.reconcile(intent, verify_tag=False)
    self.assertEqual(raised.exception.http_status, 404)
    self.assertEqual(raised.exception.broker_code, "UDAPI100010")
    self.assertNotIn("Order not found", str(raised.exception))
    self.assertNotIn("secret", repr(raised.exception))

def test_non_404_failure_stays_generically_unavailable(self):
    self.api.get_order_status.side_effect = ApiException(status=500, body="secret")
    intent = OrderIntent(1, 1, "entry", self.request, at(), broker_id="o1")
    with self.assertRaises(TradingUnavailable) as raised:
        self.broker.reconcile(intent, verify_tag=False)
    self.assertNotIsInstance(raised.exception, BrokerOrderNotFound)
    self.assertEqual(raised.exception.http_status, 500)
    self.assertIsNone(raised.exception.broker_code)
    self.assertNotIn("secret", str(raised.exception))
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run python -m unittest tests.test_trading_broker.BrokerTests.test_order_not_found_preserves_only_sanitized_metadata tests.test_trading_broker.BrokerTests.test_non_404_failure_stays_generically_unavailable -v`

Expected: FAIL because `BrokerOrderNotFound` and the metadata attributes do not exist.

- [ ] **Step 3: Implement the metadata and exception mapping**

In `app/services/trading_market.py`:

- Add `import json`, `import re`, and `from upstox_client.rest import ApiException`.
- Replace `TradingUnavailable` with a class that stores `http_status` and `broker_code`.
- Add `BrokerOrderNotFound`.
- Add a private `_failure_metadata(exc)` returning `(http_status, broker_code)`:
  - only accept `type(status) is int` in `100..599`;
  - parse `exc.body` only when it is `str`/`bytes` and at most 65536 bytes;
  - read `errors[0].get("errorCode", errors[0].get("error_code"))`;
  - accept only a string matching `UDAPI[0-9]{1,12}`;
  - never include the body or message.
- In `sdk_data`, catch `ApiException` first and raise `BrokerOrderNotFound` when `(status, code) == (404, "UDAPI100010")`; otherwise raise `TradingUnavailable` with the metadata. Keep the existing broad `except Exception` for timeouts/network errors, with no metadata.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run python -m unittest tests.test_trading_broker -v`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/services/trading_market.py tests/test_trading_broker.py
git commit -m "feat: preserve sanitized broker order-not-found metadata"
```

---

### Task 2: Poller expiry outcome

**Files:**
- Modify: `app/strategies/order_status.py`
- Modify: `app/repositories/strategy.py` (`_event` allowlist)
- Modify: `tests/test_order_status.py`

**Interfaces:**
- Produces: `OrderPollResult` frozen dataclass with `run: StrategyRun`, `verified: bool`, `expired: bool`, `position_flat: bool | None`.
- Produces: `OrderStatusPoller.poll(run: StrategyRun, now: datetime) -> OrderPollResult`.
- Consumes: `BrokerOrderNotFound` from Task 1.
- Consumes: `BrokerOrder` and `OrderIntent` existing models.

- [ ] **Step 1: Write the failing tests**

Update existing `tests/test_order_status.py` calls from `run, verified = self.poll()` to use `result = self.poll()` and `result.run` / `result.verified`. Add:

```python
def test_prior_day_expired_order_is_recorded_once_and_not_reread(self):
    self.repo.runs[1].key = RunKey(
        "client", "user", "reliance_orb", "live", at().date() - timedelta(days=1)
    )
    body = json.dumps(
        {"errors": [{"errorCode": "UDAPI100010", "message": "Order not found"}]}
    )
    self.orders.get_order_status.side_effect = ApiException(status=404, body=body)
    self.broker.portfolio.get_positions.return_value = N(status="success", data=[])

    result = self.poll()
    self.assertTrue(result.expired)
    self.assertTrue(result.verified)
    self.assertTrue(result.position_flat)
    self.assertEqual(len(self.events("order_status_expired")), 1)
    self.assertEqual(self.events("order_status_expired")[0]["broker_code"], "UDAPI100010")

    self.poll(30)
    self.assertEqual(self.orders.get_order_status.call_count, 1)
    self.assertEqual(len(self.events("position_checked")), 2)

def test_prior_day_expired_order_with_open_position_is_not_flat(self):
    self.repo.runs[1].key = RunKey(
        "client", "user", "reliance_orb", "live", at().date() - timedelta(days=1)
    )
    self.orders.get_order_status.side_effect = ApiException(
        status=404,
        body=json.dumps({"errors": [{"errorCode": "UDAPI100010"}]}),
    )
    self.broker.portfolio.get_positions.return_value = N(
        status="success",
        data=[N(instrument_token="option", product="D", quantity=500)],
    )
    result = self.poll()
    self.assertTrue(result.expired)
    self.assertFalse(result.position_flat)

def test_current_day_404_is_unverified_not_expired(self):
    self.orders.get_order_status.side_effect = ApiException(
        status=404,
        body=json.dumps({"errors": [{"errorCode": "UDAPI100010"}]}),
    )
    result = self.poll()
    self.assertFalse(result.expired)
    self.assertFalse(result.verified)
    self.assertEqual(len(self.events("order_status_unavailable")), 1)
```

Add imports `json` and `from upstox_client.rest import ApiException`.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run python -m unittest tests.test_order_status -v`

Expected: FAIL because `poll` returns a 2-tuple and has no expiry handling.

- [ ] **Step 3: Implement `OrderPollResult` and expiry handling**

In `app/strategies/order_status.py`:

- Add the dataclass with the four fields.
- Add `self.expired: set[int] = set()`.
- Change `self.checks` to cache `OrderPollResult` values.
- In `poll`, return a cached result unchanged.
- For each intent with `broker_id`:
  - Skip intents in `self.expired`.
  - Call `self.broker.reconcile(...)`.
  - On `BrokerOrderNotFound` when `run.key.exchange_date < now.astimezone(IST).date()`:
    - add the intent ID to `self.expired`;
    - set `expired = True`;
    - append `order_status_expired` with `order_id`, `broker_code`, `http_status`, and `reason`.
    - do not set `verified = False`.
  - On any other exception or `order is None`: keep the current `verified = False` and `order_status_unavailable` handling.
  - On success: keep the current `apply_order` and `order_status_checked` behavior.
- Keep the position check. Compute `actual` and set `position_flat = actual == 0`. If the position read fails, set `position_flat = None` and `verified = False`.
- If there are no intents, return `OrderPollResult(run, True, False, None)`.
- Return the result and cache it.
- Add `broker_code` and `http_status` to the `_event` allowlist in `app/repositories/strategy.py` so the expired event is persisted.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run python -m unittest tests.test_order_status -v`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/strategies/order_status.py tests/test_order_status.py
git commit -m "feat: distinguish expired prior-day broker order records"
```

---

### Task 3: Park expired runs without blocking the next session

**Files:**
- Modify: `app/repositories/strategy.py` (`STATES`, `list_unresolved`)
- Modify: `app/strategies/engine.py` (`_manage`, new `_expired_gtt_terminal`)
- Modify: `app/services/strategy_status.py`
- Modify: `tests/strategy_helpers.py`
- Test: `tests/test_order_status.py`, `tests/test_strategy_status.py`, `tests/test_strategy_engine.py`

**Interfaces:**
- Consumes: `OrderPollResult` from Task 2.
- Produces: valid state `reconciliation_expired`; excluded from `list_unresolved`.
- Produces: `StrategyEngine._expired_gtt_terminal(run) -> bool`.
- Produces: `_event` accepts `trade_ids`, `broker_code`, and `http_status` fields.

- [ ] **Step 1: Write the failing tests**

In `tests/test_order_status.py` add engine-level cases. Use a helper that builds an engine with a `MemoryGttRepository` and a terminal GTT snapshot:

```python
def test_expired_prior_day_flat_terminal_gtt_is_parked(self):
    self.repo.runs[1].key = RunKey(
        "client", "user", "reliance_orb", "live", at().date() - timedelta(days=1)
    )
    self.orders.get_order_status.side_effect = ApiException(
        status=404,
        body=json.dumps({"errors": [{"errorCode": "UDAPI100010"}]}),
    )
    self.broker.portfolio.get_positions.return_value = N(status="success", data=[])
    from app.strategies.gtt_models import GttLeg, GttRecord, GttSnapshot
    from tests.gtt_helpers import MemoryGttRepository
    gtts = MemoryGttRepository(self.repo)
    gtts.records[1] = GttRecord(
        1, self.intent.request, D(95), D(108), at(), "GTT-1",
        GttSnapshot(
            "GTT-1",
            (
                GttLeg("ENTRY", "COMPLETED", D(100), "BUY", "o1"),
                GttLeg("STOPLOSS", "CANCELLED", D(95), "SELL"),
                GttLeg("TARGET", "CANCELLED", D(108), "SELL"),
            ),
        ),
    )
    engine = StrategyEngine(
        TradingSettings(mode="live", client_id="client", user_id="user"),
        self.repo,
        self.market,
        self.broker,
        MagicMock(spec=["assert_owned"]),
        gtt_broker=MagicMock(),
        gtt_repository=gtts,
    )
    engine.tick(at())
    self.assertEqual(self.repo.get_run(1).state, "reconciliation_expired")

def test_expired_prior_day_open_position_keeps_blocking(self):
    # same prior-day + 404 setup, but positions contains quantity 500
    # and a terminal GTT snapshot exists
    ...
    engine.tick(at())
    self.assertEqual(self.repo.get_run(1).state, "reconciliation_required")
    self.assertIn("expired", self.repo.get_run(1).message)

def test_expired_prior_day_active_gtt_keeps_blocking(self):
    # same as flat case, but TARGET leg is SCHEDULED
    ...
    engine.tick(at())
    self.assertEqual(self.repo.get_run(1).state, "reconciliation_required")
```

In `tests/test_strategy_status.py` add:

```python
def test_expired_reconciliation_message_is_specific(self):
    self.run.state = "reconciliation_expired"
    result = self.status(self.run)
    self.assertIn("expired", result.message.lower())
    self.assertIn("reconciliation", result.message.lower())
```

In `tests/test_strategy_engine.py` or a repository unit test, assert `list_unresolved` excludes the state:

```python
def test_reconciliation_expired_is_not_unresolved(self):
    self.repo.runs[1].state = "reconciliation_expired"
    self.repo.runs[1].remaining_quantity = 500
    self.assertEqual(
        self.repo.list_unresolved("client", "user", "live"),
        [],
    )
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run python -m unittest tests.test_order_status tests.test_strategy_status tests.test_strategy_engine -v`

Expected: FAIL because `reconciliation_expired` is not a valid state and the engine does not park.

- [ ] **Step 3: Implement the state, engine branch, and status message**

In `app/repositories/strategy.py`:

- Add `"reconciliation_expired"` to `STATES`.
- In `list_unresolved`, add `AND state <> 'reconciliation_expired'` before the existing `AND (...)` condition.

In `app/strategies/engine.py`:

- Replace the poll call with:
  ```python
  result = self.order_status.poll(run, now)
  if result.expired:
      if not result.verified:
          return self._fail(result.run, "Broker order record expired; other checks failed; reconciliation required")
      if result.position_flat and self._expired_gtt_terminal(result.run):
          return self._state(result.run, "reconciliation_expired", message="Broker order record expired; position flat; historical reconciliation required")
      return self._fail(result.run, "Broker order record expired; open position or active GTT; reconciliation required")
  if not result.verified:
      return self._fail(result.run, "Unable to verify order status or position; reconciliation required")
  run = result.run
  ```
- Add `_expired_gtt_terminal`:
  - for GTT: return false when `self.gtt is None`; read `self.gtt.repository.get(run.id)`; return false on any exception or missing snapshot; require `snapshot.terminal` and the ENTRY leg status `COMPLETED`;
  - for non-GTT: return whether every non-entry intent is terminal.
- Import `LEG_TERMINAL` only if needed; `GttSnapshot.terminal` already covers terminal legs.

In `tests/strategy_helpers.py`, update `MemoryRepository.list_unresolved` to exclude `r.state == "reconciliation_expired"` even when `remaining_quantity > 0`.

In `app/services/strategy_status.py`, add:
```python
if run.state == "reconciliation_expired":
    message = "Broker order record expired; no open position. Historical reconciliation is still required."
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run python -m unittest tests.test_order_status tests.test_strategy_status tests.test_strategy_engine -v`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/repositories/strategy.py app/strategies/engine.py app/services/strategy_status.py tests/test_order_status.py tests/test_strategy_status.py tests/test_strategy_engine.py tests/strategy_helpers.py
git commit -m "feat: park expired prior-day runs without blocking new entries"
```

---

### Task 4: Historical reconciliation service

**Files:**
- Create: `app/services/historical_reconciliation.py`
- Create: `app/services/broker_errors.py`
- Modify: `app/repositories/order_updates.py` (add `completed_orders`)
- Test: `tests/test_historical_reconciliation.py`
- Test: `tests/test_broker_errors.py`

**Interfaces:**
- Produces:
  ```python
  @dataclass(frozen=True)
  class HistoricalOrder:
      purpose: str            # "entry" | "exit"
      request: OrderRequest
      fills: tuple[Fill, ...]
      broker_id: str | None
      fill_order_id: str
  ```
- Produces: `historical_trades(client, exchange_date: date) -> list[object]`.
- Produces: `build_repair(run: StrategyRun, intents: list[OrderIntent], manifest: dict, trades: list[object], now: datetime) -> list[HistoricalOrder]`.
- Produces: `verify_callbacks(run: StrategyRun, orders: list[HistoricalOrder], callbacks: list[dict]) -> None`.
- Produces: `OrderUpdateRepository.completed_orders(user_id: str, order_ids: list[str]) -> list[dict]`.
- Produces: `failure_details(exc: Exception, operation: str) -> dict`.
- Consumes: `Fill`, `OrderRequest`, `StrategyRun`, `OrderIntent`, `IST`, `decimal`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_historical_reconciliation.py` with fixtures modelled on this incident:

```python
class HistoricalReconciliationTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 10, 27, 9, 45, tzinfo=IST)
        self.run = StrategyRun(
            160103,
            RunKey("app", "user", "reliance_orb", "live", date(2026, 10, 26)),
            requested_quantity=1000,
            entry_reserved=True,
            execution_backend="gtt",
            state="reconciliation_required",
            contract=replace(
                contract("1190", "CE"), expiry=date(2026, 11, 23)
            ),
        )
        self.entry = OrderIntent(
            7, 160103, "entry",
            OrderRequest("gtt00fbb", "option", "D", "BUY", "LIMIT", 1000, D("45.9")),
            self.now, broker_id="261026000111597",
        )
        self.trades = [
            N(trade_id="370751", trade_date="2026-10-26", exchange="NFO", segment="FO",
              symbol="RELIANCE", option_type="CE", strike_price="1190.0",
              expiry="2026-11-23", transaction_type="BUY", quantity=500, price=45.75),
            N(trade_id="370752", trade_date="2026-10-26", exchange="NFO", segment="FO",
              symbol="RELIANCE", option_type="CE", strike_price="1190.0",
              expiry="2026-11-23", transaction_type="BUY", quantity=500, price=45.80),
            N(trade_id="753152", trade_date="2026-10-26", exchange="NFO", segment="FO",
              symbol="RELIANCE", option_type="CE", strike_price="1190.0",
              expiry="2026-11-23", transaction_type="SELL", quantity=500, price=45.95),
            N(trade_id="753153", trade_date="2026-10-26", exchange="NFO", segment="FO",
              symbol="RELIANCE", option_type="CE", strike_price="1190.0",
              expiry="2026-11-23", transaction_type="SELL", quantity=500, price=45.95),
        ]
        self.manifest = {
            "run_id": 160103,
            "orders": [
                {"purpose": "entry", "order_id": "261026000111597",
                 "trade_ids": ["370751", "370752"]},
                {"purpose": "exit", "trade_ids": ["753152", "753153"]},
            ],
        }

    def trade(self, trade_id, **changes):
        data = vars(next(t for t in self.trades if t.trade_id == trade_id)).copy()
        data.update(changes)
        return N(**data)

    def test_build_repair_uses_entry_order_and_no_exit_order_id(self):
        orders = build_repair(self.run, [self.entry], self.manifest, self.trades, self.now)
        self.assertEqual(orders[0].broker_id, "261026000111597")
        self.assertIsNone(orders[1].broker_id)
        self.assertEqual(
            (sum(f.quantity for f in orders[0].fills),
             sum(f.quantity for f in orders[1].fills)),
            (1000, 1000),
        )
        self.assertNotEqual(orders[1].fill_order_id, "261026000111597")

    def test_wrong_date_contract_side_or_quantity_rejects(self):
        for field, value in (
            ("trade_date", "2026-10-25"),
            ("strike_price", "1195.0"),
            ("transaction_type", "BUY"),
            ("quantity", 400),
        ):
            with self.subTest(field=field):
                trades = [
                    self.trade(t.trade_id, **{field: value})
                    if t.trade_id == "753152" else t
                    for t in self.trades
                ]
                with self.assertRaises(ValueError):
                    build_repair(self.run, [self.entry], self.manifest, trades, self.now)

    def test_duplicate_trade_assignment_rejects(self):
        manifest = deepcopy(self.manifest)
        manifest["orders"][1]["trade_ids"].append("370751")
        with self.assertRaises(ValueError):
            build_repair(self.run, [self.entry], manifest, self.trades, self.now)

    def test_missing_callback_is_allowed_but_conflicting_callback_rejects(self):
        orders = build_repair(self.run, [self.entry], self.manifest, self.trades, self.now)
        verify_callbacks(self.run, orders, [])
        conflicting = [{
            "order_id": "261026000111597",
            "user_id": "user",
            "instrument_token": "option",
            "product": "D",
            "transaction_type": "BUY",
            "status": "complete",
            "quantity": 1000,
            "filled_quantity": 1000,
            "average_price": 99.0,
            "order_timestamp": "2026-10-26 10:00:03",
        }]
        with self.assertRaises(ValueError):
            verify_callbacks(self.run, orders, conflicting)
```

Create `tests/test_broker_errors.py` with a 404 `ApiException` and a timeout, asserting only allowlisted fields appear and no message/body.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run python -m unittest tests.test_historical_reconciliation tests.test_broker_errors -v`

Expected: FAIL with `ModuleNotFoundError` for the new modules.

- [ ] **Step 3: Implement the service and broker error helper**

`app/services/broker_errors.py`:
- Allowlist exception type names: `ApiException`, `TimeoutError`, `ConnectionError`, `ValueError`, `TypeError`, `AttributeError`, `OperationalError`, `IntegrityError`, `TradingUnavailable`, `BrokerOrderNotFound`.
- Accept `http_status` only when `type` is `int` in `100..599`.
- Accept `broker_code` only when it matches `UDAPI[0-9]{1,12}`.
- Read `exc.http_status` / `exc.broker_code` and, as a fallback, parse `ApiException` metadata with the same rules as Task 1.
- Never include `str(exc)`, `exc.body`, headers, or arbitrary details.

`app/services/historical_reconciliation.py`:
- `historical_trades` calls `sdk.PostTradeApi(client).get_trades_by_date_range(str(date), str(date), page, 100, segment="FO")` through `sdk_data`, paginates until fewer than 100 rows, and caps at 1000 pages.
- `build_repair` validates the run, manifest, and each trade exactly as the spec describes. Use `Decimal(str(...))` for strike and price, and `date.fromisoformat(str(...))` for expiry.
- Entry: require `entry_intent.broker_id == spec["order_id"]`; reuse `entry_intent.request`; fills use `entry_intent.broker_id`.
- Exit: build `OrderRequest` with a unique `hist` tag, side `SELL`, quantity equal to the trade total, and weighted average price; `broker_id=None`; `fill_order_id` = `histfill` + 16 hex characters.
- `verify_callbacks` compares only when a callback exists; missing callbacks are allowed.

`app/repositories/order_updates.py`:
- Add `OrderUpdateRepository.completed_orders(self, user_id, order_ids)` using placeholders for the ID list and returning parsed `payload` dicts. Never interpolate IDs.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run python -m unittest tests.test_historical_reconciliation tests.test_broker_errors -v`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/services/historical_reconciliation.py app/services/broker_errors.py app/repositories/order_updates.py tests/test_historical_reconciliation.py tests/test_broker_errors.py
git commit -m "feat: validate operator-attributed historical fills"
```

---

### Task 5: Atomic repository repair

**Files:**
- Modify: `app/repositories/strategy.py` (`repair_closed`)
- Test: `tests/test_historical_repository.py`

**Interfaces:**
- Produces: `StrategyRepository.repair_closed(run: StrategyRun, orders: list[HistoricalOrder], *, apply: bool = False) -> dict`.
- Consumes: `HistoricalOrder` from Task 4.
- Returns: `{"applied": bool, "run_id": int, "state": str, "filled_quantity": int, "remaining_quantity": int, "entry_average": Decimal | None, "orders": list[dict]}`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_historical_repository.py`. Build the `HistoricalOrder` fixtures with the Task 4 test fixture and `build_repair` rather than hard-coding fills. Patch the MySQL boundaries with `MagicMock` (`get_connection`, `_read`, `_intents`, and the GTT snapshot query with `fetchone`/`fetchall`). Cover:

```python
def test_dry_run_has_no_insert_update_or_delete(self):
    result = self.repair(apply=False)
    self.assertEqual((result["state"], result["filled_quantity"], result["remaining_quantity"]),
                     ("closed", 1000, 0))
    statements = [c.args[0].lstrip().upper() for c in self.cursor.execute.call_args_list]
    self.assertFalse(any(s.startswith(("INSERT", "UPDATE", "DELETE")) for s in statements))

def test_apply_commits_entry_exit_fills_and_closed_state(self):
    result = self.repair(apply=True)
    self.assertTrue(result["applied"])
    self.conn.commit.assert_called_once()
    self.conn.rollback.assert_not_called()
    # assert the exit order was inserted with broker_id NULL and status complete
    # assert four fills were inserted
    # assert the run was saved as closed

def test_stale_version_or_pending_mutation_refuses(self):
    # _read returns a newer version -> ValueError; no commit
    # pending mutation row -> ValueError; no commit

def test_conflicting_fill_refuses_even_on_dry_run(self):
    # an existing fill for trade 370751 with a different price -> ValueError

def test_identical_replay_is_idempotent_and_writes_nothing(self):
    # run already closed with the same intents/fills -> no INSERT/UPDATE/DELETE
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run python -m unittest tests.test_historical_repository -v`

Expected: FAIL because `repair_closed` does not exist.

- [ ] **Step 3: Implement `repair_closed`**

Implementation requirements:

- Reject unless:
  - `run.key.mode == "live"`, `run.execution_backend == "gtt"`, `run.entry_reserved`, `run.contract is not None`, `run.requested_quantity > 0`;
  - `run.state in {"reconciliation_required", "reconciliation_expired", "closed"}`;
  - the manifest provides exactly one `entry` and one `exit` `HistoricalOrder`.
- Open one transaction. For apply, `_read(c, run.id, True)`; for dry run, no lock.
- Compare `current.version` with `run.version`; reject a mismatch.
- Read `strategy_gtts` for the run. Reject when:
  - the row is missing;
  - `snapshot` is missing;
  - the ENTRY leg order ID is not the entry order's `broker_id`;
  - any leg is not terminal (`COMPLETED`, `CANCELLED`, `EXPIRED`, `FAILED`).
- Reject a pending `strategy_gtt_mutations` row.
- Read existing intents with `_intents(c, run.id)`.
- Entry:
  - require exactly one entry intent and `intent.broker_id == entry.broker_id`;
  - require `intent.status in {"unknown", "complete"}`;
  - if existing fills are present, require the same `trade_id`/`order_id`/`quantity`/`price` set as the repair.
- Exit:
  - accept at most one existing `exit` intent; if present, require the same request tag/quantity/side and the same fills; otherwise insert a new intent with `broker_id=NULL`, `status='complete'`, `submitted_at=datetime.now(UTC)`, and the unique `hist…` tag.
- Fills:
  - query by `(client_id,user_id,mode,trade_id)`; if present, require the same `(intent_id, order_id, quantity, price)`; otherwise insert.
  - entry fills use `order_id=entry.broker_id`; exit fills use `exit.fill_order_id`.
- Update the entry intent to `status='complete'`.
- Recompute the projected run with `reduce_fills` and set `state='closed'`, `message='Historical manual exit reconciled'`.
- For apply, `_save(c, projected, current.version)`.
- Insert a `historical_reconciled` event with `order_id`, `status='complete'`, `filled_quantity`, `trade_ids`, and `reason='historical manual exit'`.
- For dry run, compute the same projection but write nothing.
- Repeating an identical closed repair returns the current summary with `applied=False` and no writes.
- Add `trade_ids` to the `_event` allowlist in `app/repositories/strategy.py`.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run python -m unittest tests.test_historical_repository -v`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/repositories/strategy.py tests/test_historical_repository.py
git commit -m "feat: apply atomic historical run repairs"
```

---

### Task 6: Operator reconciliation CLI

**Files:**
- Create: `app/cli/reconcile_history.py`
- Test: `tests/test_reconcile_cli.py`

**Interfaces:**
- Produces: `reconcile(manifest: dict, *, apply: bool = False) -> dict`.
- Produces: `main(argv=None) -> int`.
- Consumes: Tasks 4 and 5, `WorkerLock`, `get_token`, `get_upstox_config`, `Broker`, `OrderUpdateRepository`, `failure_details`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_reconcile_cli.py` covering:

```python
def test_default_is_read_only_and_takes_no_worker_lock(self):
    reconcile({"run_id": 160103})
    self.assertFalse(self.repo.repair_closed.call_args.kwargs["apply"])
    self.lock.acquire.assert_not_called()

def test_apply_refuses_when_worker_lock_is_held(self):
    self.lock.acquire.return_value = False
    with self.assertRaises(ValueError):
        reconcile({"run_id": 160103}, apply=True)
    self.repo.repair_closed.assert_not_called()

def test_wrong_account_or_nonflat_position_refuses(self):
    # profile.user_id mismatch -> ValueError
    # positions contains the contract -> ValueError
    self.repo.repair_closed.assert_not_called()

def test_resources_close_on_error(self):
    # make build_repair raise; assert client.pool.join called
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run python -m unittest tests.test_reconcile_cli -v`

Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Implement the CLI**

- `reconcile(manifest, *, apply=False)`:
  - `run = StrategyRepository().get_run(manifest["run_id"])`.
  - verify the configured app owns the run;
  - load the stored live token for the run account; fail if absent;
  - build `sdk.ApiClient`, disable transport retries;
  - verify `UserApi.get_profile("2.0").user_id == run.key.user_id`;
  - fetch historical trades for `run.key.exchange_date`;
  - build repairs from `repo.list_intents(run.id)`;
  - cross-check callbacks for known order IDs;
  - reject any non-zero position in `run.contract.instrument_key` with product `D`;
  - if apply: acquire `WorkerLock(..., "live")`, re-read the run, then call `repair_closed(..., apply=True)`;
  - otherwise call `repair_closed(..., apply=False)`.
- `main`:
  - argparse: positional `manifest` path, `--apply`;
  - print `json.dumps(result, default=str, indent=2)`;
  - on exception print `{"error": "Historical repair refused; no partial repair committed", **failure_details(exc, "historical_repair")}` and return 1;
  - always close the SDK client resources in `finally`.
- No broker mutation methods are called.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run python -m unittest tests.test_reconcile_cli -v`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/cli/reconcile_history.py tests/test_reconcile_cli.py
git commit -m "feat: add dry-run-first historical reconciliation CLI"
```

---

### Task 7: Manifest, documentation, and full-suite verification

**Files:**
- Create: `reports/reconciliation/2026-10-26-run-160103.json`
- Modify: `README.md`
- Modify: `deploy/README.md`
- Test: full suite

**Interfaces:**
- Consumes: Task 6 CLI.

- [ ] **Step 1: Add the incident manifest**

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

- [ ] **Step 2: Document the state and CLI**

In `README.md`, add a section covering:

- `reconciliation_expired`: prior-day order record expired, position flat, GTT terminal; does not block new runs; historical repair still required.
- The CLI dry run and `--apply` commands.
- The manifest format and the rule that the sell order ID may be absent.
- The requirement to stop the worker before apply.
- The rule that non-flat positions and active GTTs still block.

In `deploy/README.md`, add the production sequence: stop worker, dry run, apply, verify, restart after the session close.

- [ ] **Step 3: Run the full suite**

Run: `uv run python -m unittest discover -v`

Expected: all tests pass, MySQL-gated tests skipped unless `ORB_MYSQL_TESTS=1`.

- [ ] **Step 4: Run the targeted tests again**

Run: `uv run python -m unittest tests.test_order_status tests.test_historical_reconciliation tests.test_historical_repository tests.test_reconcile_cli -v`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add reports/reconciliation/2026-10-26-run-160103.json README.md deploy/README.md
git commit -m "docs: document expired-order recovery and repair runbook"
```

---

### Task 8: Production repair and verification

**Files:** none (production data operation).

**Interfaces:**
- Consumes: Task 6 CLI, Task 7 manifest.
- Requires: user has stopped the production worker.

- [ ] **Step 1: Ask the user to stop the worker**

On the production host:

```bash
docker compose stop worker
docker compose ps
```

Confirm the worker is stopped. Do not proceed until the user confirms.

- [ ] **Step 2: Verify the worker lock is free**

Run the local CLI dry run; if the worker is still running, `--apply` will refuse. To check the lock directly, print its name with the same code the worker uses:

```bash
uv run python - <<'PY'
from app.repositories.strategy import StrategyRepository
from app.repositories.worker_lock import WorkerLock

run = StrategyRepository().get_run(160103)
print(WorkerLock(run.key.client_id, run.key.user_id, run.key.mode).name)
PY
```

Then run `SELECT IS_USED_LOCK('<printed name>') AS held;` against the production database. `held` must be `NULL`.

- [ ] **Step 3: Dry run against production**

```bash
PYTHONPATH=. uv run python -m app.cli.reconcile_history reports/reconciliation/2026-10-26-run-160103.json
```

Expected JSON: `state="closed"`, `filled_quantity=1000`, `remaining_quantity=0`, `entry_average=45.775`, four trade IDs, entry order `261026000111597`, exit order ID absent.

- [ ] **Step 4: Apply**

```bash
PYTHONPATH=. uv run python -m app.cli.reconcile_history reports/reconciliation/2026-10-26-run-160103.json --apply
```

Expected: `applied=true` and the same figures.

- [ ] **Step 5: Verify the database**

```sql
SELECT id, state, version, requested_quantity, filled_quantity,
       remaining_quantity, entry_average, message
FROM strategy_runs WHERE id = 160103;

SELECT id, purpose, broker_id, status, tag
FROM strategy_orders WHERE run_id = 160103 ORDER BY id;

SELECT f.trade_id, f.order_id, f.quantity, f.price
FROM strategy_fills f
JOIN strategy_orders o ON o.id = f.intent_id
WHERE o.run_id = 160103 ORDER BY f.trade_id;

SELECT kind, fields, created_at
FROM strategy_events WHERE run_id = 160103 ORDER BY id DESC LIMIT 5;
```

Expected: state `closed`; filled 1000; remaining 0; entry average 45.775; entry intent complete with broker `261026000111597`; exit intent complete with `broker_id IS NULL`; four fills; a `historical_reconciled` event.

- [ ] **Step 6: Confirm no live exposure**

Use the existing read-only checks: positions empty, order book/trades for today empty (or only the operator’s non-strategy activity). Do not submit or cancel anything.

- [ ] **Step 7: Restart after the session close**

After 15:30 IST today:

```bash
docker compose up -d worker
docker compose logs --tail 50 worker
```

Confirm the worker heartbeat is current and today’s run is `skipped`. Tomorrow, confirm a new run is created and the strategy can submit its order. Deployment of the new code is separate; the DB repair alone restores next-session trading.
