import unittest
from datetime import UTC, datetime
from decimal import Decimal as D
from types import SimpleNamespace as N
from unittest.mock import MagicMock, patch

from app.strategies.gtt_models import GttLeg, GttRecord
from app.strategies.models import OrderRequest


class GttBrokerTests(unittest.TestCase):
    def setUp(self):
        from app.services.gtt_broker import GttBroker

        self.api = MagicMock()
        self.orders = MagicMock()
        self.broker = GttBroker(MagicMock(), api=self.api, orders=self.orders)
        self.request = OrderRequest(
            "gtttest", "NSE_FO|123", "D", "BUY", "LIMIT", 500, D(100)
        )
        self.record = GttRecord(
            1, self.request, D(90), D(118), datetime.now(UTC), "GTT-123"
        )
        self.gate = patch("app.services.trading_market.clock.sleep")
        self.gate.start()
        self.addCleanup(self.gate.stop)

    def test_place_includes_both_server_side_exits(self):
        self.api.place_gtt_order.return_value = N(
            status="success", data=N(gtt_order_ids=["GTT-123"])
        )
        self.assertEqual(self.broker.place(self.record), "GTT-123")
        body = self.api.place_gtt_order.call_args.kwargs["body"]
        self.assertEqual(
            (body.type, body.quantity, body.instrument_token),
            ("MULTIPLE", 500, "NSE_FO|123"),
        )
        self.assertEqual(
            [
                (r.strategy, r.trigger_type, r.trigger_price, r.market_protection)
                for r in body.rules
            ],
            [
                ("ENTRY", "IMMEDIATE", 100.0, -1),
                ("TARGET", "IMMEDIATE", 118.0, -1),
                ("STOPLOSS", "IMMEDIATE", 90.0, -1),
            ],
        )

    def test_creation_never_retries_and_sanitizes_exception(self):
        self.api.place_gtt_order.side_effect = TimeoutError("private-token")
        with self.assertRaisesRegex(RuntimeError, "uncertain") as caught:
            self.broker.place(self.record)
        self.assertNotIn("private-token", str(caught.exception))
        self.assertEqual(self.api.place_gtt_order.call_count, 1)

    def test_snapshot_validates_parent_and_all_legs(self):
        raw = N(
            gtt_order_id="GTT-123",
            type="MULTIPLE",
            quantity=500,
            product="D",
            instrument_token="NSE_FO|123",
            rules=[
                N(
                    strategy=s,
                    status="SCHEDULED",
                    trigger_price=p,
                    transaction_type=side,
                    order_id=None,
                    trigger_type="IMMEDIATE",
                )
                for s, p, side in [
                    ("ENTRY", 100, "BUY"),
                    ("TARGET", 118, "SELL"),
                    ("STOPLOSS", 90, "SELL"),
                ]
            ],
        )
        self.api.get_gtt_order_details.return_value = N(status="success", data=[raw])
        self.assertFalse(self.broker.snapshot(self.record).terminal)
        raw.instrument_token = "other"
        with self.assertRaises(ValueError):
            self.broker.snapshot(self.record)

    def test_generated_child_checks_identity_and_trade_evidence(self):
        leg = GttLeg("ENTRY", "COMPLETED", D(100), "BUY", "o1")
        raw = N(
            order_id="o1",
            instrument_token="NSE_FO|123",
            product="D",
            transaction_type="BUY",
            quantity=500,
            status="complete",
            filled_quantity=500,
        )
        self.orders.get_order_status.return_value = N(status="success", data=raw)
        trade = N(trade_id="t1", order_id="o1", quantity=500, average_price=99.5)
        self.orders.get_trades_by_order.return_value = N(
            status="success", data=[trade, trade]
        )
        order = self.broker.child_order(self.record, leg, self.request)
        self.assertEqual((order.filled_quantity, len(order.fills)), (500, 1))
        raw.transaction_type = "SELL"
        with self.assertRaises(ValueError):
            self.broker.child_order(self.record, leg, self.request)

    def test_modify_and_cancel_use_parent_id(self):
        self.api.modify_gtt_order.return_value = N(
            status="success", data=N(gtt_order_ids=["GTT-123"])
        )
        self.api.cancel_gtt_order.return_value = N(
            status="success", data=N(gtt_order_ids=["GTT-123"])
        )
        self.broker.modify(self.record, D("89.5"), D("117.5"))
        self.assertEqual(
            self.api.modify_gtt_order.call_args.kwargs["body"].gtt_order_id, "GTT-123"
        )
        self.broker.cancel(self.record)
        self.assertEqual(
            self.api.cancel_gtt_order.call_args.kwargs["body"].gtt_order_id, "GTT-123"
        )

    def test_completed_leg_requires_complete_exchange_fill(self):
        leg = GttLeg("ENTRY", "COMPLETED", D(100), "BUY", "o1")
        raw = N(
            order_id="o1",
            instrument_token="NSE_FO|123",
            product="D",
            transaction_type="BUY",
            quantity=500,
            status="open",
            filled_quantity=0,
        )
        self.orders.get_order_status.return_value = N(status="success", data=raw)
        with self.assertRaises(ValueError):
            self.broker.child_order(self.record, leg, self.request)

    def test_child_status_rejects_wrong_id_and_incomplete_fills(self):
        leg = GttLeg("ENTRY", "COMPLETED", D(100), "BUY", "o1")
        raw = N(
            order_id="other",
            instrument_token="NSE_FO|123",
            product="D",
            transaction_type="BUY",
            quantity=500,
            filled_quantity=500,
            status="complete",
        )
        self.orders.get_order_status.return_value = N(status="success", data=raw)
        with self.assertRaises(ValueError):
            self.broker.child_order(self.record, leg, self.request)
        raw.order_id = "o1"
        self.orders.get_trades_by_order.return_value = N(status="success", data=[])
        with self.assertRaisesRegex(ValueError, "Incomplete broker trade evidence"):
            self.broker.child_order(self.record, leg, self.request)
        self.orders.get_order_book.assert_not_called()
