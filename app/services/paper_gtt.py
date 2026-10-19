"""Deterministic GTT simulation over PaperBroker; never calls a broker API."""

from dataclasses import replace

from app.strategies.gtt_models import GttLeg, GttSnapshot
from app.strategies.models import OrderRequest


class PaperGttBroker:
    def __init__(self, paper):
        self.paper = paper
        self.parents = {}
        self.records = {}

    def place(self, record):
        parent_id = "GTT-paper-" + record.request.tag
        if parent_id in self.parents:
            raise ValueError("Duplicate simulated GTT")
        self.records[parent_id] = record
        entry = self.paper.submit(record.request)
        self.parents[parent_id] = GttSnapshot(
            parent_id,
            (
                GttLeg(
                    "ENTRY",
                    "COMPLETED" if entry.status == "complete" else "OPEN",
                    record.request.price,
                    "BUY",
                    entry.broker_id,
                ),
                GttLeg("TARGET", "SCHEDULED", record.target, "SELL"),
                GttLeg("STOPLOSS", "SCHEDULED", record.stop, "SELL"),
            ),
        )
        return parent_id

    def restore(self, record, intents):
        self.paper.restore(intents)
        self.records[record.broker_id] = record
        if record.broker_id not in self.parents:
            if record.snapshot:
                self.parents[record.broker_id] = record.snapshot
            else:
                # Before first observation, deterministic ID ties simulated recovery
                # to the original reservation; no real exchange request exists.
                parent_id = self.place(record)
                if parent_id != record.broker_id:
                    raise ValueError("Simulated parent identity mismatch")

    def snapshot(self, record):
        parent = self.parents[record.broker_id]
        legs = []
        for leg in parent.legs:
            if leg.order_id:
                order = self.paper._evaluate(self.paper.orders[leg.order_id])
                status = {
                    "complete": "COMPLETED",
                    "cancelled": "CANCELLED",
                    "rejected": "FAILED",
                }.get(order.status, "OPEN")
                leg = replace(leg, status=status)
            legs.append(leg)
        entry = next(leg for leg in legs if leg.strategy == "ENTRY")
        q = self.paper.quote_provider(record.request.instrument)
        fresh = q is not None and (
            self.paper.clock is None
            or q.fresh(self.paper.clock(), self.paper.quote_max_age_seconds)
        )
        if entry.status == "COMPLETED" and fresh:
            selected = next(
                (
                    leg
                    for leg in legs
                    if leg.status == "SCHEDULED"
                    and (
                        (leg.strategy == "TARGET" and q.bid >= leg.price)
                        or (leg.strategy == "STOPLOSS" and q.bid <= leg.price)
                    )
                ),
                None,
            )
            if selected:
                # Model one linked exit, without modelling exchange queue/impact.
                price = selected.price if selected.strategy == "TARGET" else q.bid
                request = OrderRequest(
                    record.request.tag[:17]
                    + ("tp" if selected.strategy == "TARGET" else "sl"),
                    record.request.instrument,
                    "D",
                    "SELL",
                    "LIMIT",
                    record.request.quantity,
                    price,
                )
                order = self.paper.submit(request)
                legs = [
                    replace(
                        leg,
                        order_id=order.broker_id,
                        status="COMPLETED" if order.status == "complete" else "OPEN",
                    )
                    if leg.strategy == selected.strategy
                    else replace(leg, status="CANCELLED")
                    if leg.strategy != "ENTRY"
                    else leg
                    for leg in legs
                ]
        result = GttSnapshot(record.broker_id, tuple(legs))
        self.parents[record.broker_id] = result
        return result

    def modify(self, record, stop, target):
        parent = self.parents[record.broker_id]
        self.parents[record.broker_id] = replace(
            parent,
            legs=tuple(
                replace(leg, price=stop if leg.strategy == "STOPLOSS" else target)
                if leg.strategy != "ENTRY"
                else leg
                for leg in parent.legs
            ),
        )

    def cancel(self, record):
        parent = self.parents[record.broker_id]
        self.parents[record.broker_id] = replace(
            parent,
            legs=tuple(
                replace(leg, status="CANCELLED")
                if leg.status not in {"COMPLETED", "FAILED", "EXPIRED", "CANCELLED"}
                and not leg.order_id
                else leg
                for leg in parent.legs
            ),
        )

    def child_order(self, record, leg, request):
        return replace(
            self.paper._evaluate(self.paper.orders[leg.order_id]), tag=request.tag
        )
