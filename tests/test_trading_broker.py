import unittest
from dataclasses import replace
from decimal import Decimal as D
from types import SimpleNamespace as N
from unittest.mock import MagicMock

from app.core.trading import TradingSettings
from app.services.trading_broker import Broker, TradingUnavailable, validate_entry
from app.strategies.models import EntryLimits, OrderIntent, OrderRequest, Quote
from tests.test_strategy_rules import at, contract


class BrokerTests(unittest.TestCase):
    def setUp(self):
        self.client = MagicMock()
        self.api = MagicMock()
        self.trades = MagicMock()
        self.broker = Broker(self.client, orders=self.api)
        self.request = OrderRequest(
            "orb123", "option", "D", "BUY", "LIMIT", 500, D(100)
        )

    def test_submission_is_D_limit_and_accepted_is_not_filled(self):
        self.api.place_order.return_value = N(status="success", data=N(order_id="o1"))
        order = self.broker.submit(self.request)
        body = self.api.place_order.call_args.args[0]
        self.assertEqual(
            (body.product, body.quantity, body.price, body.order_type),
            ("D", 500, 100.0, "LIMIT"),
        )
        self.assertEqual((order.status, order.filled_quantity), ("unknown", 0))

    def test_timeout_is_unknown_and_never_retried(self):
        self.api.place_order.side_effect = TimeoutError("secret")
        with self.assertRaisesRegex(TradingUnavailable, "uncertain"):
            self.broker.submit(self.request)
        self.assertEqual(self.api.place_order.call_count, 1)

    def test_reconcile_by_tag_requires_complete_unique_trade_evidence(self):
        intent = OrderIntent(1, 1, "entry", self.request, at())
        raw = N(
            order_id="o1",
            tag="orb123",
            instrument_token="option",
            product="D",
            transaction_type="BUY",
            quantity=500,
            filled_quantity=500,
            status="complete",
        )
        self.api.get_order_book.return_value = N(status="success", data=[raw])
        trade = N(trade_id="t1", order_id="o1", quantity=500, average_price=100)
        self.api.get_trades_by_order.return_value = N(
            status="success", data=[trade, trade]
        )
        result = self.broker.reconcile(intent)
        self.assertEqual(
            (result.status, result.filled_quantity, len(result.fills)),
            ("complete", 500, 1),
        )
        self.api.get_order_book.return_value = N(status="success", data=[])
        self.assertIsNone(self.broker.reconcile(intent))

    def test_entry_limits_funds_and_fresh_quotes(self):
        limits = EntryLimits(D(150), D(60000), D(6000), D(2), D(1))
        s = TradingSettings(limits=limits)
        q = Quote("option", at(), D(99), D(100), D(100))
        self.assertEqual(validate_entry(contract(), q, at(), s, D(60000)), D(100))
        for funds, quote in [
            (D(100), q),
            (D(60000), replace(q, timestamp=at(9, 30))),
            (D(60000), replace(q, ask=D(160))),
        ]:
            with self.assertRaises(ValueError):
                validate_entry(contract(), quote, at(), s, funds)

    def test_uncapped_spend_still_enforces_funds_spread_and_planned_loss(self):
        settings = TradingSettings(
            mode="live",
            live_enabled=True,
            limits=EntryLimits(None, None, D(5000), D(1), D(1)),
        )
        quote = Quote("option", at(), D(999), D(1000), D(1000))
        self.assertEqual(
            validate_entry(contract(), quote, at(), settings, D(500000)), D(1000)
        )
        cases = [
            (contract(), quote, D(499999)),
            (contract(), replace(quote, bid=D(998)), D(500000)),
            (replace(contract(), lot_size=501), quote, D(1002000)),
        ]
        for selected, pricing, funds in cases:
            with (
                self.subTest(funds=funds, quantity=selected.quantity),
                self.assertRaises(ValueError),
            ):
                validate_entry(selected, pricing, at(), settings, funds)

    def test_two_lots_pass_at_5000_planned_loss_but_not_below(self):
        selected = replace(contract(), lot_size=500)
        quote = Quote("option", at(), D("18.95"), D(19), D(19))
        limits = EntryLimits(None, None, D(5000), D(1), D(1))
        settings = TradingSettings(limits=limits)
        self.assertEqual(
            validate_entry(selected, quote, at(), settings, D(19000)), D(19)
        )
        with self.assertRaisesRegex(ValueError, "planned loss limit"):
            validate_entry(
                selected,
                quote,
                at(),
                replace(settings, limits=replace(limits, max_planned_loss=D(4999))),
                D(19000),
            )

    def test_protection_flag_alone_does_not_override_missing_account_permissions(self):
        broker = Broker(self.client, orders=self.api, protective_stop_verified=True)
        self.assertFalse(broker.supports_protective_stop(contract()))
        broker.profile = N(
            is_active=True,
            exchanges=["NFO"],
            products=["D"],
            order_types=["LIMIT", "SL"],
        )
        self.assertTrue(broker.supports_protective_stop(contract()))
        broker.profile.exchanges = ["NSE"]
        self.assertFalse(broker.supports_protective_stop(contract()))
