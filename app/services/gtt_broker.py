"""Official SDK GTT boundary; all mutations are single-attempt calls."""

import re

import upstox_client as sdk

from app.services.trading_market import TradingUnavailable, decimal, sdk_data
from app.strategies.gtt_models import GttLeg, GttSnapshot
from app.strategies.models import BrokerOrder, Fill


def _identity(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", value):
        raise ValueError("Invalid broker identity")
    return value


class GttBroker:
    def __init__(self, client, *, api=None, orders=None):
        self.api = api or sdk.OrderApiV3(client)
        self.orders = orders or sdk.OrderApi(client)

    def _rules(self, record, stop, target):
        return [
            sdk.GttRule(
                strategy=s,
                trigger_type="IMMEDIATE",
                trigger_price=float(p),
                market_protection=-1,
            )
            for s, p in [
                ("ENTRY", record.request.price),
                ("TARGET", target),
                ("STOPLOSS", stop),
            ]
        ]

    def _ack(self, data, expected=None):
        ids = getattr(data, "gtt_order_ids", None)
        if not isinstance(ids, list) or len(ids) != 1:
            raise TradingUnavailable("GTT acknowledgement uncertain")
        value = _identity(ids[0])
        if not value.startswith("GTT-") or (expected and expected != value):
            raise TradingUnavailable("GTT acknowledgement uncertain")
        return value

    def place(self, record):
        req = record.request
        body = sdk.GttPlaceOrderRequest(
            type="MULTIPLE",
            instrument_token=req.instrument,
            product="D",
            quantity=req.quantity,
            rules=self._rules(record, record.stop, record.target),
            transaction_type="BUY",
        )
        return self._ack(sdk_data(self.api.place_gtt_order, body=body))

    def modify(self, record, stop, target):
        body = sdk.GttModifyOrderRequest(
            type="MULTIPLE",
            quantity=record.request.quantity,
            rules=self._rules(record, stop, target),
            gtt_order_id=record.broker_id,
        )
        self._ack(sdk_data(self.api.modify_gtt_order, body=body), record.broker_id)

    def cancel(self, record):
        self._ack(
            sdk_data(
                self.api.cancel_gtt_order,
                body=sdk.GttCancelOrderRequest(gtt_order_id=record.broker_id),
            ),
            record.broker_id,
        )

    def snapshot(self, record):
        data = sdk_data(self.api.get_gtt_order_details, gtt_order_id=record.broker_id)
        if not isinstance(data, list) or len(data) != 1:
            raise TradingUnavailable("GTT identity ambiguous")
        raw = data[0]
        req = record.request
        if (
            raw.gtt_order_id,
            raw.type,
            raw.instrument_token,
            raw.product,
            raw.quantity,
        ) != (record.broker_id, "MULTIPLE", req.instrument, "D", req.quantity):
            raise ValueError("GTT does not match reserved intent")
        legs = []
        for rule in raw.rules:
            if rule.trigger_type != "IMMEDIATE":
                raise ValueError("Unexpected GTT trigger type")
            order_id = _identity(rule.order_id) if rule.order_id else None
            legs.append(
                GttLeg(
                    rule.strategy,
                    rule.status,
                    decimal(rule.trigger_price),
                    rule.transaction_type,
                    order_id,
                )
            )
        snapshot = GttSnapshot(record.broker_id, tuple(legs))
        entry = next(leg for leg in snapshot.legs if leg.strategy == "ENTRY")
        if entry.price != req.price:
            raise ValueError("GTT entry price changed externally")
        return snapshot

    def child_order(self, record, leg, request):
        """Read fills only for the exact exchange order linked by this GTT leg."""
        raw_orders = sdk_data(self.orders.get_order_book, "2.0")
        matches = [o for o in raw_orders if o.order_id == leg.order_id]
        if len(matches) != 1:
            raise TradingUnavailable("GTT child order unavailable or ambiguous")
        raw = matches[0]
        if (raw.instrument_token, raw.product, raw.transaction_type, raw.quantity) != (
            record.request.instrument,
            "D",
            leg.side,
            record.request.quantity,
        ):
            raise ValueError("GTT child does not match parent")
        status = {
            "complete": "complete",
            "cancelled": "cancelled",
            "rejected": "rejected",
            "open": "open",
            "trigger pending": "trigger_pending",
        }.get(str(raw.status).lower(), "unknown")
        if (status == "complete" and raw.filled_quantity != raw.quantity) or (
            leg.status == "COMPLETED" and status != "complete"
        ):
            raise ValueError("GTT completion conflicts with exchange order")
        fills = {}
        if raw.filled_quantity:
            for trade in sdk_data(self.orders.get_trades_by_order, leg.order_id, "2.0"):
                fill = Fill(
                    _identity(trade.trade_id),
                    _identity(trade.order_id),
                    trade.quantity,
                    decimal(trade.average_price),
                )
                if fill.order_id != leg.order_id or (
                    fill.trade_id in fills and fills[fill.trade_id] != fill
                ):
                    raise ValueError("Conflicting GTT trade evidence")
                fills[fill.trade_id] = fill
        return BrokerOrder(
            leg.order_id,
            request.tag,
            status,
            raw.quantity,
            raw.filled_quantity,
            tuple(fills.values()),
        )
