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

    def list_unresolved(self, client_id, user_id, mode):
        with transaction() as c:
            c.execute(
                "SELECT * FROM strategy_runs WHERE client_id=%s AND user_id=%s AND mode=%s AND (remaining_quantity>0 OR state IN ('entry_pending','exit_pending','reconciliation_required','open')) ORDER BY exchange_date,id",
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
            for k, v in changes.items():
                setattr(run, k, v)
            run.state = state
            return _save(c, run, expected_version)

    def heartbeat(self, run_id, now):
        with transaction() as c:
            c.execute(
                "UPDATE strategy_runs SET heartbeat=%s WHERE id=%s", (utc(now), run_id)
            )

    def _insert(self, c, run, purpose, request):
        if purpose not in {"entry", "stop", "exit"}:
            raise ValueError("Invalid order purpose")
        if request.instrument != run.contract.instrument_key or request.product != "D":
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

    def _event(self, c, run_id, kind, fields):
        allowed = {"order_id", "status", "filled_quantity", "reason", "state"}
        fields = {k: v for k, v in fields.items() if k in allowed}
        c.execute(
            "INSERT INTO strategy_events (run_id,created_at,kind,fields) VALUES (%s,%s,%s,%s)",
            (run_id, utc(datetime.now(UTC)), kind, dump(fields)),
        )

    def append_event(self, run_id, kind, fields):
        with transaction() as c:
            self._event(c, run_id, kind, fields)
