"""Transactional GTT ownership; broker calls never occur inside transactions."""

from dataclasses import asdict
from datetime import UTC, datetime
from decimal import Decimal

from app.repositories.strategy import (
    _intents,
    _read,
    _save,
    aware,
    dump,
    load,
    transaction,
    utc,
)
from app.strategies.gtt_models import GttLeg, GttRecord, GttSnapshot
from app.strategies.models import TERMINAL, OrderRequest


def record_from_row(row, pending=None):
    req = load(row["request"])
    req = {**req, **{k: Decimal(req[k]) for k in ("price", "trigger_price")}}
    snapshot = load(row["snapshot"]) if row.get("snapshot") else None
    if snapshot:
        snapshot = GttSnapshot(
            snapshot["broker_id"],
            tuple(
                GttLeg(**{**leg, "price": Decimal(leg["price"])})
                for leg in snapshot["legs"]
            ),
        )
    if pending:
        pending = {**pending, "fields": load(pending["fields"])}
    return GttRecord(
        row["run_id"],
        OrderRequest(**req),
        Decimal(row["stop"]),
        Decimal(row["target"]),
        aware(row["created_at"]),
        row["broker_id"],
        snapshot,
        pending,
        row.get("attempt", 1),
        bool(row.get("retry_requested", False)),
        bool(row.get("retry_blocked", False)),
    )


class GttRepository:
    def _get(self, c, run_id):
        c.execute("SELECT * FROM strategy_gtts WHERE run_id=%s", (run_id,))
        row = c.fetchone()
        if row is None:
            raise ValueError("Missing GTT reservation")
        c.execute(
            "SELECT id,kind,fields FROM strategy_gtt_mutations WHERE run_id=%s AND attempt=%s AND status='pending' ORDER BY id",
            (run_id, row["attempt"]),
        )
        pending = c.fetchall()
        if len(pending) > 1:
            raise ValueError("Conflicting GTT mutations")
        return record_from_row(row, pending[0] if pending else None)

    def get(self, run_id):
        with transaction() as c:
            return self._get(c, run_id)

    def block_retries(self, run_id):
        """Uncertainty is a durable disqualification, even after reads recover."""
        with transaction() as c:
            _read(c, run_id, True)
            c.execute(
                "UPDATE strategy_gtts SET retry_blocked=1 WHERE run_id=%s", (run_id,)
            )

    def authorize_retry(self, run, record):
        """Persist timeout intent before cancellation so recovery retains its cause."""
        with transaction() as c:
            current = _read(c, run.id, True)
            saved = self._get(c, run.id)
            if (
                current.version != run.version
                or current.state == "reconciliation_required"
                or current.filled_quantity
                or current.remaining_quantity
                or saved.attempt != record.attempt
                or saved.attempt >= 3
                or saved.pending
                or saved.retry_blocked
                or not saved.broker_id
            ):
                raise ValueError("GTT retry authorization unavailable")
            c.execute(
                "UPDATE strategy_gtts SET retry_requested=1 WHERE run_id=%s", (run.id,)
            )

    def reserve_retry(self, run, previous, request, stop, target):
        """Archive the resolved parent and reserve its successor in one transaction."""
        with transaction() as c:
            current = _read(c, run.id, True)
            saved = self._get(c, run.id)
            if current.version != run.version or saved.attempt != previous.attempt:
                return None
            intents = _intents(c, run.id)
            if (
                not saved.retry_requested
                or saved.attempt >= 3
                or saved.pending
                or saved.retry_blocked
                or current.state != "entry_pending"
                or current.filled_quantity
                or current.remaining_quantity
                or not saved.snapshot
                or not saved.snapshot.terminal
                or not saved.broker_id
                or any(leg.status != "CANCELLED" for leg in saved.snapshot.legs)
                or any(
                    i.status not in TERMINAL or i.status == "rejected" or i.fills
                    for i in intents
                )
                or request.instrument != current.contract.instrument_key
                or request.quantity != current.requested_quantity
                or request.side != "BUY"
                or not stop < request.price < target
            ):
                raise ValueError("Prior GTT entry not safe to retry")
            c.execute(
                "INSERT INTO strategy_gtt_attempt_history (run_id,attempt,request,stop,target,created_at,broker_id,snapshot) SELECT run_id,attempt,request,stop,target,created_at,broker_id,snapshot FROM strategy_gtts WHERE run_id=%s",
                (run.id,),
            )
            now = datetime.now(UTC)
            attempt = saved.attempt + 1
            c.execute(
                "UPDATE strategy_gtts SET attempt=%s,retry_requested=0,request=%s,stop=%s,target=%s,created_at=%s,broker_id=NULL,snapshot=NULL WHERE run_id=%s",
                (attempt, dump(asdict(request)), stop, target, utc(now), run.id),
            )
            c.execute(
                "INSERT INTO strategy_gtt_mutations (run_id,attempt,kind,fields,created_at) VALUES (%s,%s,'create',%s,%s)",
                (
                    run.id,
                    attempt,
                    dump({"request": asdict(request), "stop": stop, "target": target}),
                    utc(now),
                ),
            )
            record = GttRecord(run.id, request, stop, target, now, attempt=attempt)
            record.pending = {"id": c.lastrowid, "kind": "create", "fields": {}}
            current.stop, current.target = stop, target
            _save(c, current, current.version)
            return record

    def reserve(self, run, request, stop, target):
        now = datetime.now(UTC)
        with transaction() as c:
            current = _read(c, run.id, True)
            if current.entry_reserved or current.version != run.version:
                return None
            if (
                current.state != "waiting_signal"
                or request.instrument != current.contract.instrument_key
                or request.quantity != current.contract.quantity
                or request.side != "BUY"
            ):
                raise ValueError("Invalid GTT reservation")
            if not stop < request.price < target:
                raise ValueError("Invalid initial GTT levels")
            record = GttRecord(run.id, request, stop, target, now)
            c.execute(
                "INSERT INTO strategy_gtts (run_id,request,stop,target,created_at) VALUES (%s,%s,%s,%s,%s)",
                (run.id, dump(asdict(request)), stop, target, utc(now)),
            )
            c.execute(
                "INSERT INTO strategy_gtt_mutations (run_id,kind,fields,created_at) VALUES (%s,'create',%s,%s)",
                (
                    run.id,
                    dump({"request": asdict(request), "stop": stop, "target": target}),
                    utc(now),
                ),
            )
            record.pending = {"id": c.lastrowid, "kind": "create", "fields": {}}
            current.entry_reserved = True
            current.requested_quantity = request.quantity
            current.execution_backend = "gtt"
            current.state = "entry_pending"
            current.stop, current.target = stop, target
            _save(c, current, current.version)
            return record

    def acknowledge(self, run_id, broker_id):
        with transaction() as c:
            _read(c, run_id, True)
            c.execute(
                "SELECT broker_id FROM strategy_gtts WHERE run_id=%s FOR UPDATE",
                (run_id,),
            )
            row = c.fetchone()
            if row is None or (row["broker_id"] and row["broker_id"] != broker_id):
                raise ValueError("Conflicting GTT identity")
            c.execute(
                "UPDATE strategy_gtts SET broker_id=%s WHERE run_id=%s",
                (broker_id, run_id),
            )
            c.execute(
                "UPDATE strategy_gtt_mutations SET status='confirmed' WHERE run_id=%s AND attempt=(SELECT attempt FROM strategy_gtts WHERE run_id=%s) AND kind='create' AND status='pending'",
                (run_id, run_id),
            )

    def mutation(self, run_id, kind, fields):
        if kind not in {"modify", "cancel", "cancel_child"}:
            raise ValueError("Invalid GTT mutation")
        with transaction() as c:
            _read(c, run_id, True)
            c.execute(
                "SELECT id,kind FROM strategy_gtt_mutations WHERE run_id=%s AND attempt=(SELECT attempt FROM strategy_gtts WHERE run_id=%s) AND status='pending' LIMIT 1",
                (run_id, run_id),
            )
            pending = c.fetchone()
            if pending:
                if kind != "cancel" or pending["kind"] != "modify":
                    raise ValueError("Unresolved GTT mutation")
                # Cancellation supersedes a protection update, never creation or
                # another cancellation. Both journal changes commit atomically.
                c.execute(
                    "UPDATE strategy_gtt_mutations SET status='superseded' WHERE id=%s",
                    (pending["id"],),
                )
            c.execute(
                "INSERT INTO strategy_gtt_mutations (run_id,attempt,kind,fields,created_at) SELECT run_id,attempt,%s,%s,%s FROM strategy_gtts WHERE run_id=%s",
                (kind, dump(fields), utc(datetime.now(UTC)), run_id),
            )
            return c.lastrowid

    def mutation_result(self, mutation_id, status):
        if status not in {"confirmed", "not_submitted", "superseded"}:
            raise ValueError("Unknown mutation outcome remains pending")
        with transaction() as c:
            c.execute(
                "UPDATE strategy_gtt_mutations SET status=%s WHERE id=%s",
                (status, mutation_id),
            )

    def observe(self, run_id, snapshot):
        with transaction() as c:
            _read(c, run_id, True)
            c.execute(
                "UPDATE strategy_gtts SET snapshot=%s WHERE run_id=%s AND broker_id=%s",
                (dump(asdict(snapshot)), run_id, snapshot.broker_id),
            )

    def levels(self, run_id, stop, target):
        with transaction() as c:
            _read(c, run_id, True)
            c.execute(
                "UPDATE strategy_gtts SET stop=%s,target=%s WHERE run_id=%s",
                (stop, target, run_id),
            )

    def attach_child(self, run_id, leg, request):
        """Import a broker-generated child; never authorizes submission of this intent."""
        purpose = {"ENTRY": "entry", "STOPLOSS": "stop", "TARGET": "target"}[
            leg.strategy
        ]
        with transaction() as c:
            run = _read(c, run_id, True)
            if (
                run.execution_backend != "gtt"
                or request.instrument != run.contract.instrument_key
                or request.quantity != run.requested_quantity
                or request.side != leg.side
            ):
                raise ValueError("GTT child does not match reserved run")
            c.execute(
                "SELECT order_id,intent_id FROM strategy_gtt_legs WHERE run_id=%s AND attempt=(SELECT attempt FROM strategy_gtts WHERE run_id=%s) AND strategy=%s",
                (run_id, run_id, leg.strategy),
            )
            existing = c.fetchone()
            if existing:
                if existing["order_id"] != leg.order_id:
                    raise ValueError(
                        "GTT generated a replacement child requiring reconciliation"
                    )
                return next(
                    i for i in _intents(c, run_id) if i.id == existing["intent_id"]
                )
            if any(i.broker_id == leg.order_id for i in _intents(c, run_id)):
                raise ValueError("GTT child already assigned")
            c.execute(
                "INSERT INTO strategy_orders (run_id,purpose,tag,request,submitted_at,broker_id) VALUES (%s,%s,%s,%s,%s,%s)",
                (
                    run_id,
                    purpose,
                    request.tag,
                    dump(asdict(request)),
                    utc(datetime.now(UTC)),
                    leg.order_id,
                ),
            )
            intent_id = c.lastrowid
            c.execute(
                "INSERT INTO strategy_gtt_legs (run_id,attempt,strategy,order_id,intent_id) SELECT run_id,attempt,%s,%s,%s FROM strategy_gtts WHERE run_id=%s",
                (leg.strategy, leg.order_id, intent_id, run_id),
            )
            return next(i for i in _intents(c, run_id) if i.id == intent_id)
