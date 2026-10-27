"""Transactional strategy persistence. No schema creation or broker calls."""

import json
from contextlib import contextmanager
from dataclasses import asdict, replace
from datetime import UTC, date, datetime
from decimal import Decimal

import pymysql

from app.repositories.database import get_connection
from app.strategies.models import (
    TERMINAL,
    Contract,
    Fill,
    OpeningRange,
    OrderIntent,
    OrderRequest,
    RunKey,
    StrategyRun,
)

STATES = {
    "waiting_range",
    "waiting_signal",
    "entry_pending",
    "open",
    "exit_pending",
    "closed",
    "skipped",
    "rejected",
    "reconciliation_required",
    "gtt_active",
    "closing",
}
ALLOWED = {
    "opening",
    "processed_candle",
    "contract",
    "requested_quantity",
    "filled_quantity",
    "remaining_quantity",
    "entry_average",
    "stop",
    "target",
    "message",
    "entry_reserved",
    "execution_backend",
}


def utc(value):
    return value.astimezone(UTC).replace(tzinfo=None) if value else None


def aware(value):
    return value.replace(tzinfo=UTC) if value and value.tzinfo is None else value


def encode(value):
    if isinstance(value, (Decimal, date, datetime)):
        return str(value)
    raise TypeError("Unsupported stored value")


def dump(value):
    return json.dumps(value, default=encode, separators=(",", ":"))


def load(value):
    return json.loads(value) if isinstance(value, str) else value


def run_from_row(row):
    key = RunKey(
        *(row[k] for k in ("client_id", "user_id", "strategy", "mode", "exchange_date"))
    )
    run = StrategyRun(
        row["id"],
        key,
        version=row.get("version", 0),
        state=row.get("state", "waiting_range"),
    )
    for key in ALLOWED - {"opening", "contract", "processed_candle", "heartbeat"}:
        if key in row:
            setattr(run, key, row[key])
    run.entry_reserved = bool(run.entry_reserved)
    if row.get("opening_high") is not None:
        run.opening = OpeningRange(row["opening_high"], row["opening_low"])
    for key in ("heartbeat", "processed_candle"):
        setattr(run, key, aware(row.get(key)))
    if row.get("contract"):
        data = load(row["contract"])
        data["expiry"] = date.fromisoformat(data["expiry"])
        for k in ("strike", "tick_size"):
            data[k] = Decimal(data[k])
        run.contract = Contract(**data)
    return run


def reduce_fills(run, intents):
    seen = {}
    buys = []
    sells = []
    for intent in intents:
        for fill in intent.fills:
            if fill.trade_id in seen:
                if seen[fill.trade_id] != fill:
                    raise ValueError("Conflicting trade evidence")
                continue
            seen[fill.trade_id] = fill
            (buys if intent.purpose == "entry" else sells).append(fill)
    bought = sum(f.quantity for f in buys)
    sold = sum(f.quantity for f in sells)
    if sold > bought:
        raise ValueError("Strategy exposure mismatch")
    average = (
        sum((f.price * f.quantity for f in buys), Decimal(0)) / bought
        if bought
        else None
    )
    return replace(
        run,
        filled_quantity=bought,
        remaining_quantity=bought - sold,
        entry_average=average,
    )


@contextmanager
def transaction():
    conn = get_connection()
    try:
        with conn.cursor(pymysql.cursors.DictCursor) as c:
            yield c
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _read(c, run_id, lock=False):
    c.execute(
        "SELECT * FROM strategy_runs WHERE id=%s" + (" FOR UPDATE" if lock else ""),
        (run_id,),
    )
    row = c.fetchone()
    if not row:
        raise ValueError("Strategy run missing")
    return run_from_row(row)


def _save(c, run, expected):
    if run.state not in STATES:
        raise ValueError("Invalid strategy state")
    columns = {k: getattr(run, k) for k in ALLOWED - {"opening", "contract"}}
    columns.update(
        opening_high=run.opening.high if run.opening else None,
        opening_low=run.opening.low if run.opening else None,
        contract=dump(asdict(run.contract)) if run.contract else None,
    )
    # Heartbeat writes have their own method and must survive older run snapshots.
    columns["processed_candle"] = utc(columns["processed_candle"])
    query = ", ".join(f"`{k}`=%s" for k in columns)
    c.execute(
        f"UPDATE strategy_runs SET {query}, state=%s, version=version+1 WHERE id=%s AND version=%s",
        (*columns.values(), run.state, run.id, expected),
    )
    if c.rowcount != 1:
        raise RuntimeError("Concurrent strategy transition")
    run.version = expected + 1
    return run


def _intents(c, run_id):
    c.execute("SELECT * FROM strategy_orders WHERE run_id=%s ORDER BY id", (run_id,))
    rows = c.fetchall()
    result = []
    for row in rows:
        req = load(row["request"])
        for k in ("price", "trigger_price"):
            req[k] = Decimal(req[k])
        c.execute(
            "SELECT trade_id,order_id,quantity,price FROM strategy_fills WHERE intent_id=%s ORDER BY id",
            (row["id"],),
        )
        fills = [Fill(**f) for f in c.fetchall()]
        result.append(
            OrderIntent(
                row["id"],
                run_id,
                row["purpose"],
                OrderRequest(**req),
                aware(row["submitted_at"]),
                row["broker_id"],
                row["status"],
                fills,
            )
        )
    return result


class StrategyRepository:
    def get_or_create(self, key):
        with transaction() as c:
            c.execute(
                "INSERT INTO strategy_runs (client_id,user_id,strategy,mode,exchange_date) VALUES (%s,%s,%s,%s,%s) ON DUPLICATE KEY UPDATE id=LAST_INSERT_ID(id)",
                (key.client_id, key.user_id, key.strategy, key.mode, key.exchange_date),
            )
            c.execute(
                "SELECT * FROM strategy_runs WHERE client_id=%s AND user_id=%s AND strategy=%s AND mode=%s AND exchange_date=%s",
                (key.client_id, key.user_id, key.strategy, key.mode, key.exchange_date),
            )
            return run_from_row(c.fetchone())

    def get_run(self, run_id):
        with transaction() as c:
            return _read(c, run_id)

    def list_intents(self, run_id):
        with transaction() as c:
            return _intents(c, run_id)

    def record_decision(self, run_id, decision, observed_at):
        with transaction() as c:
            c.execute(
                "SELECT * FROM strategy_signals WHERE run_id=%s AND candle_close=%s FOR UPDATE",
                (run_id, utc(decision.candle_close)),
            )
            previous = c.fetchone()
            evidence = {
                key: getattr(decision, key)
                for key in ("close_price", "opening_high", "opening_low", "direction")
            }
            if previous:
                same_evidence = all(
                    previous[key] == value for key, value in evidence.items()
                )
                if same_evidence and (
                    previous["outcome"] == decision.outcome
                    or (
                        previous["outcome"] in {"signal", "no_signal"}
                        and decision.outcome in {"expired", "processed", "cutoff"}
                    )
                ):
                    return False
                self._event(
                    c,
                    run_id,
                    "decision_revised",
                    {
                        "candle_close": decision.candle_close,
                        "state": decision.outcome,
                        "reason": previous["outcome"] + " -> " + decision.outcome,
                        "previous_close": previous["close_price"],
                        "previous_opening_high": previous["opening_high"],
                        "previous_opening_low": previous["opening_low"],
                        "direction": previous["direction"],
                    },
                )
                c.execute(
                    "UPDATE strategy_signals SET observed_at=%s,close_price=%s,opening_high=%s,opening_low=%s,direction=%s,outcome=%s WHERE id=%s",
                    (
                        utc(observed_at),
                        decision.close_price,
                        decision.opening_high,
                        decision.opening_low,
                        decision.direction,
                        decision.outcome,
                        previous["id"],
                    ),
                )
                return True
            c.execute(
                "INSERT INTO strategy_signals (run_id,observed_at,candle_start,candle_close,close_price,opening_high,opening_low,direction,outcome) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (
                    run_id,
                    utc(observed_at),
                    utc(decision.candle_start),
                    utc(decision.candle_close),
                    decision.close_price,
                    decision.opening_high,
                    decision.opening_low,
                    decision.direction,
                    decision.outcome,
                ),
            )
            return c.rowcount == 1

    def history_runs(self, client_id, user_id, mode, *, page=1):
        if mode not in {"paper", "live"} or not 1 <= page <= 10000:
            raise ValueError("Invalid history selection")
        with transaction() as c:
            c.execute(
                "SELECT * FROM strategy_runs WHERE client_id=%s AND user_id=%s AND mode=%s ORDER BY exchange_date DESC,id DESC LIMIT 20 OFFSET %s",
                (client_id, user_id, mode, (page - 1) * 20),
            )
            return [run_from_row(row) for row in c.fetchall()]

    def history_details(self, run_id):
        # Call only after selecting a run from the account-scoped history page.
        with transaction() as c:
            c.execute(
                "SELECT * FROM strategy_signals WHERE run_id=%s ORDER BY candle_close DESC LIMIT 30",
                (run_id,),
            )
            decisions = [
                dict(
                    row,
                    **{
                        key: aware(row[key])
                        for key in ("candle_start", "candle_close", "observed_at")
                    },
                )
                for row in c.fetchall()
            ]
            orders = _intents(c, run_id)
            c.execute(
                "SELECT created_at,kind,fields FROM strategy_events WHERE run_id=%s ORDER BY id DESC LIMIT 100",
                (run_id,),
            )
            events = [
                dict(
                    row, created_at=aware(row["created_at"]), fields=load(row["fields"])
                )
                for row in c.fetchall()
            ]
            return decisions, orders, events

    def list_unresolved(self, client_id, user_id, mode):
        with transaction() as c:
            c.execute(
                "SELECT * FROM strategy_runs WHERE client_id=%s AND user_id=%s AND mode=%s AND (remaining_quantity>0 OR state IN ('entry_pending','exit_pending','reconciliation_required','open','gtt_active','closing')) ORDER BY exchange_date,id",
                (client_id, user_id, mode),
            )
            return [run_from_row(r) for r in c.fetchall()]

    def latest(self, client_id, user_id, mode):
        with transaction() as c:
            c.execute(
                "SELECT * FROM strategy_runs WHERE client_id=%s AND user_id=%s AND mode=%s ORDER BY exchange_date DESC,id DESC LIMIT 1",
                (client_id, user_id, mode),
            )
            row = c.fetchone()
            return run_from_row(row) if row else None

    def transition(self, run_id, expected_version, state, changes):
        if state not in STATES or set(changes) - ALLOWED:
            raise ValueError("Invalid strategy transition fields")
        with transaction() as c:
            run = _read(c, run_id, True)
            if run.version != expected_version:
                raise RuntimeError("Stale strategy state")
            changed = (
                run.state != state or changes.get("message", run.message) != run.message
            )
            for k, v in changes.items():
                setattr(run, k, v)
            run.state = state
            result = _save(c, run, expected_version)
            if changed:
                self._event(
                    c, run.id, "state_changed", {"state": state, "reason": run.message}
                )
            return result

    def heartbeat(self, run_id, now):
        with transaction() as c:
            c.execute(
                "UPDATE strategy_runs SET heartbeat=%s WHERE id=%s", (utc(now), run_id)
            )

    def _insert(self, c, run, purpose, request):
        if purpose not in {"entry", "stop", "exit"}:
            raise ValueError("Invalid order purpose")
        if (
            request.instrument != run.contract.instrument_key
            or request.product not in {"I", "D"}
        ):
            raise ValueError("Contract mismatch")
        existing = _intents(c, run.id)
        if purpose != "entry" and any(
            i.purpose != "entry" and i.status not in TERMINAL for i in existing
        ):
            raise RuntimeError("Outstanding sell intent")
        if purpose != "entry" and request.quantity > run.remaining_quantity:
            raise ValueError("Excess exit quantity")
        if purpose == "entry" and request.quantity != run.contract.quantity:
            raise ValueError("Entry must be two lots")
        now = datetime.now(UTC)
        c.execute(
            "INSERT INTO strategy_orders (run_id,purpose,tag,request,submitted_at) VALUES (%s,%s,%s,%s,%s)",
            (run.id, purpose, request.tag, dump(asdict(request)), utc(now)),
        )
        return OrderIntent(c.lastrowid, run.id, purpose, request, now)

    def reserve_entry(self, run_id, expected_version, request):
        with transaction() as c:
            run = _read(c, run_id, True)
            if (
                run.entry_reserved
                or run.version != expected_version
                or run.state not in {"waiting_signal", "waiting_range"}
            ):
                return None
            intent = self._insert(c, run, "entry", request)
            run.entry_reserved = True
            run.state = "entry_pending"
            run.requested_quantity = request.quantity
            _save(c, run, expected_version)
            return intent

    def create_intent(self, run_id, expected_version, purpose, request):
        with transaction() as c:
            run = _read(c, run_id, True)
            if run.version != expected_version:
                raise RuntimeError("Stale strategy state")
            intent = self._insert(c, run, purpose, request)
            run.state = "exit_pending" if purpose == "exit" else "open"
            _save(c, run, expected_version)
            return intent

    def apply_order(self, intent_id, order):
        with transaction() as c:
            return self._apply_order(c, intent_id, order)

    def _apply_order(self, c, intent_id, order):
        c.execute("SELECT run_id FROM strategy_orders WHERE id=%s", (intent_id,))
        row = c.fetchone()
        if not row:
            raise ValueError("Missing order intent")
        run = _read(c, row["run_id"], True)
        intent = next(i for i in _intents(c, run.id) if i.id == intent_id)
        if (
            order.tag != intent.request.tag
            or order.requested_quantity != intent.request.quantity
            or (intent.broker_id and intent.broker_id != order.broker_id)
        ):
            raise ValueError("Order identity mismatch")
        if intent.status in TERMINAL and order.status not in TERMINAL:
            raise ValueError("Order status regression")
        for f in order.fills:
            if f.order_id != order.broker_id:
                raise ValueError("Trade order mismatch")
            c.execute(
                "SELECT intent_id,order_id,quantity,price FROM strategy_fills WHERE client_id=%s AND user_id=%s AND mode=%s AND trade_id=%s",
                (run.key.client_id, run.key.user_id, run.key.mode, f.trade_id),
            )
            prev = c.fetchone()
            if prev:
                if (
                    prev["intent_id"],
                    prev["order_id"],
                    prev["quantity"],
                    prev["price"],
                ) != (intent_id, f.order_id, f.quantity, f.price):
                    raise ValueError("Conflicting stored trade")
            else:
                c.execute(
                    "INSERT INTO strategy_fills (intent_id,client_id,user_id,mode,trade_id,order_id,quantity,price) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
                    (
                        intent_id,
                        run.key.client_id,
                        run.key.user_id,
                        run.key.mode,
                        f.trade_id,
                        f.order_id,
                        f.quantity,
                        f.price,
                    ),
                )
        c.execute(
            "UPDATE strategy_orders SET broker_id=%s,status=%s WHERE id=%s",
            (order.broker_id, order.status, intent_id),
        )
        intents = _intents(c, run.id)
        run = reduce_fills(run, intents)
        if run.filled_quantity > run.requested_quantity:
            raise ValueError("Excess entry fills")
        _save(c, run, run.version)
        self._event(
            c,
            run.id,
            "order_status",
            {
                "order_id": order.broker_id,
                "status": order.status,
                "filled_quantity": order.filled_quantity,
            },
        )
        return run

    def record_submission(self, intent_id, broker_id):
        with transaction() as c:
            c.execute(
                "UPDATE strategy_orders SET broker_id=%s, status='unknown' WHERE id=%s",
                (broker_id, intent_id),
            )

    @staticmethod
    def _fill_set(fills):
        return {(f.trade_id, f.order_id, f.quantity, f.price) for f in fills}

    def _validate_manual_exit(
        self, run, intents, entry_intent, entry_order, exit_request, exit_order
    ):
        if (
            run.key.mode != "live"
            or run.execution_backend != "gtt"
            or not run.entry_reserved
            or run.contract is None
        ):
            raise ValueError("Manual exit repair requires a live GTT run")
        if run.state not in {"reconciliation_required", "closed"}:
            raise ValueError("Run is not awaiting manual reconciliation")
        if run.requested_quantity != entry_order.requested_quantity:
            raise ValueError("Entry quantity mismatch")
        if (
            entry_intent.broker_id != entry_order.broker_id
            or entry_intent.request.tag != entry_order.tag
            or entry_intent.request.quantity != entry_order.requested_quantity
        ):
            raise ValueError("Entry order identity mismatch")
        if entry_intent.status not in {"unknown", "complete"}:
            raise ValueError("Entry status not repairable")
        if (
            entry_order.status != "complete"
            or entry_order.filled_quantity != entry_order.requested_quantity
            or sum(f.quantity for f in entry_order.fills)
            != entry_order.filled_quantity
            or any(f.order_id != entry_order.broker_id for f in entry_order.fills)
        ):
            raise ValueError("Entry must be fully filled")
        if entry_intent.fills and self._fill_set(
            entry_intent.fills
        ) != self._fill_set(entry_order.fills):
            raise ValueError("Conflicting entry fills")
        if (
            exit_request.instrument != run.contract.instrument_key
            or exit_request.product != "D"
            or exit_request.side != "SELL"
            or exit_request.order_type != "LIMIT"
            or exit_request.quantity != entry_order.filled_quantity
        ):
            raise ValueError("Exit request mismatch")
        if (
            not exit_order.broker_id
            or exit_order.tag != exit_request.tag
            or exit_order.status != "complete"
            or exit_order.requested_quantity != exit_request.quantity
            or exit_order.filled_quantity != exit_request.quantity
            or sum(f.quantity for f in exit_order.fills)
            != exit_order.filled_quantity
            or any(f.order_id != exit_order.broker_id for f in exit_order.fills)
        ):
            raise ValueError("Exit order mismatch")
        trade_ids = [f.trade_id for f in entry_order.fills + exit_order.fills]
        if len(trade_ids) != len(set(trade_ids)):
            raise ValueError("Duplicate trade evidence")
        exits = [i for i in intents if i.purpose == "exit"]
        if exits:
            existing = exits[-1]
            if (
                existing.request.tag != exit_request.tag
                or existing.request.quantity != exit_request.quantity
                or self._fill_set(existing.fills)
                != self._fill_set(exit_order.fills)
            ):
                raise ValueError("Conflicting exit fills")
        elif run.state == "closed":
            raise ValueError("Closed run missing exit evidence")

    def _manual_exit_summary(self, run, entry_order, exit_order, *, applied):
        quantity = sum(f.quantity for f in entry_order.fills)
        average = sum(
            (f.price * f.quantity for f in entry_order.fills), Decimal(0)
        ) / quantity
        return {
            "applied": applied,
            "run_id": run.id,
            "state": "closed",
            "filled_quantity": quantity,
            "remaining_quantity": 0,
            "entry_average": average,
            "orders": [
                {
                    "purpose": "entry",
                    "order_id": entry_order.broker_id,
                    "trade_ids": [f.trade_id for f in entry_order.fills],
                },
                {
                    "purpose": "exit",
                    "order_id": exit_order.broker_id,
                    "trade_ids": [f.trade_id for f in exit_order.fills],
                },
            ],
        }

    def repair_manual_exit(
        self, run, entry_order, exit_request, exit_order, *, apply=False
    ):
        intents = self.list_intents(run.id)
        entry_intent = next(
            (i for i in intents if i.purpose == "entry"), None
        )
        if entry_intent is None:
            raise ValueError("Entry intent missing")
        self._validate_manual_exit(
            run, intents, entry_intent, entry_order, exit_request, exit_order
        )
        if run.state == "closed" or not apply:
            return self._manual_exit_summary(
                run, entry_order, exit_order, applied=False
            )
        with transaction() as c:
            current = _read(c, run.id, True)
            if current.version != run.version:
                raise ValueError("Stale strategy run")
            current_intents = _intents(c, run.id)
            current_entry = next(
                (i for i in current_intents if i.purpose == "entry"), None
            )
            if current_entry is None:
                raise ValueError("Entry intent missing")
            self._validate_manual_exit(
                current,
                current_intents,
                current_entry,
                entry_order,
                exit_request,
                exit_order,
            )
            entry_run = self._apply_order(c, current_entry.id, entry_order)
            exit_intent = self._insert(c, entry_run, "exit", exit_request)
            flat_run = self._apply_order(c, exit_intent.id, exit_order)
            flat_run.state = "closed"
            flat_run.message = "Manual exit 2026-10-26 reconciled"
            _save(c, flat_run, flat_run.version)
            self._event(
                c,
                run.id,
                "historical_reconciled",
                {
                    "order_id": entry_order.broker_id,
                    "status": "complete",
                    "filled_quantity": flat_run.filled_quantity,
                    "trade_ids": [
                        f.trade_id
                        for f in entry_order.fills + exit_order.fills
                    ],
                    "reason": "manual exit",
                },
            )
            return self._manual_exit_summary(
                flat_run, entry_order, exit_order, applied=True
            )

    def _event(self, c, run_id, kind, fields):
        allowed = {
            "order_id",
            "status",
            "filled_quantity",
            "reason",
            "state",
            "candle_close",
            "direction",
            "instrument",
            "strike",
            "expiry",
            "quantity",
            "expected_quantity",
            "actual_quantity",
            "price",
            "previous_close",
            "previous_opening_high",
            "previous_opening_low",
            "trade_ids",
        }
        fields = {k: v for k, v in fields.items() if k in allowed}
        c.execute(
            "INSERT INTO strategy_events (run_id,created_at,kind,fields) VALUES (%s,%s,%s,%s)",
            (run_id, utc(datetime.now(UTC)), kind, dump(fields)),
        )

    def append_event(self, run_id, kind, fields):
        with transaction() as c:
            self._event(c, run_id, kind, fields)
