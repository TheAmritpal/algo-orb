"""Live polling uses real SDK adapters with mocked HTTP and in-memory persistence."""

import unittest
from datetime import timedelta
from decimal import Decimal as D
from types import SimpleNamespace as N
from unittest.mock import MagicMock, patch

import upstox_client as sdk

from app.core.trading import TradingSettings
from app.services.trading_broker import Broker
from app.strategies.engine import StrategyEngine
from app.strategies.models import OrderRequest, RunKey
from tests.strategy_helpers import FakeMarket, MemoryRepository
from tests.test_strategy_rules import at


class OrderStatusTests(unittest.TestCase):
    def setUp(self):
        from app.strategies.order_status import OrderStatusPoller

        self.repo = MemoryRepository()
        self.market = FakeMarket()
        run = self.repo.get_or_create(
            RunKey("client", "user", "reliance_orb", "live", at().date())
        )
        run = self.repo.transition(
            run.id,
            run.version,
            "waiting_signal",
            {
                "contract": self.market.contract,
                "execution_backend": "gtt",
            },
        )
        req = OrderRequest("localgtttag", "option", "D", "BUY", "LIMIT", 500, D(100))
        self.intent = self.repo.reserve_entry(run.id, run.version, req)
        self.repo.intents[self.intent.id].broker_id = "o1"
        self.orders = MagicMock()
        self.broker = Broker(MagicMock(), orders=self.orders)
        self.broker.portfolio = MagicMock()
        self.raw = sdk.OrderBookData(
            order_id="o1",
            tag=None,
            instrument_token="option",
            product="D",
            transaction_type="BUY",
            quantity=500,
            filled_quantity=500,
            status="complete",
        )
        self.orders.get_order_status.return_value = sdk.GetOrderDetailsResponse(
            status="success", data=self.raw
        )
        self.orders.get_trades_by_order.return_value = sdk.GetTradeResponse(
            status="success",
            data=[
                sdk.TradeData(
                    trade_id="t1", order_id="o1", quantity=500, average_price=99.5
                ),
            ],
        )
        self.broker.portfolio.get_positions.return_value = N(
            status="success",
            data=[
                N(instrument_token="option", product="D", quantity=500),
            ],
        )
        self.poller = OrderStatusPoller(self.repo, self.broker)
        gate = patch("app.services.trading_market.clock.sleep")
        gate.start()
        self.addCleanup(gate.stop)

    def poll(self, seconds=0):
        return self.poller.poll(self.repo.get_run(1), at() + timedelta(seconds=seconds))

    def events(self, kind):
        return [fields for _, k, fields in self.repo.events if k == kind]

    def test_thirty_second_checks_persist_fills_even_for_completed_entry(self):
        run, verified = self.poll()
        self.assertTrue(verified)
        self.assertEqual((run.entry_average, run.remaining_quantity), (D("99.5"), 500))
        self.assertEqual(self.repo.list_intents(1)[0].status, "complete")
        self.poll(29)
        self.assertEqual(len(self.events("order_status_checked")), 1)
        self.poll(30)
        self.poll(59)
        self.poll(60)
        self.assertEqual(len(self.events("order_status_checked")), 3)
        self.assertEqual(self.orders.get_order_status.call_count, 3)
        self.assertEqual(len(self.repo.list_intents(1)[0].fills), 1)

    def test_restart_checks_immediately(self):
        from app.strategies.order_status import OrderStatusPoller

        self.poll()
        self.poller = OrderStatusPoller(self.repo, self.broker)
        self.poll(1)
        self.assertEqual(len(self.events("order_status_checked")), 2)

    def test_reconciliation_still_records_status_when_gtt_snapshot_fails(self):
        from app.strategies.gtt_models import GttRecord
        from tests.gtt_helpers import MemoryGttRepository

        run = self.repo.get_run(1)
        self.repo.transition(1, run.version, "reconciliation_required", {})
        gtts = MemoryGttRepository(self.repo)
        gtts.records[1] = GttRecord(
            1, self.intent.request, D(95), D(108), at(), "GTT-1"
        )
        adapter = MagicMock()
        adapter.snapshot.side_effect = TimeoutError("private-token")
        engine = StrategyEngine(
            TradingSettings(mode="live", client_id="client", user_id="user"),
            self.repo,
            self.market,
            self.broker,
            MagicMock(spec=["assert_owned"]),
            gtt_broker=adapter,
            gtt_repository=gtts,
        )
        engine.tick(at())
        run = self.repo.get_run(1)
        self.assertEqual((run.entry_average, run.remaining_quantity), (D("99.5"), 500))
        self.assertEqual(run.state, "reconciliation_required")
        self.assertTrue(gtts.get(1).retry_blocked)
        self.broker.orders.place_order.assert_not_called()
        self.assertEqual(len(self.events("order_status_checked")), 1)

    def test_failed_read_blocks_execution_and_retries_after_thirty_seconds(self):
        self.orders.get_order_status.side_effect = TimeoutError("private-token")
        engine = StrategyEngine(
            TradingSettings(mode="live", client_id="client", user_id="user"),
            self.repo,
            self.market,
            self.broker,
            MagicMock(spec=["assert_owned"]),
        )
        engine.tick(at())
        engine.tick(at() + timedelta(seconds=29))
        self.assertEqual(self.repo.get_run(1).state, "reconciliation_required")
        self.assertEqual(len(self.events("order_status_unavailable")), 1)
        self.assertNotIn("private-token", str(self.repo.events))
        self.broker.orders.place_order.assert_not_called()
        self.broker.orders.cancel_order.assert_not_called()
        self.orders.get_order_status.side_effect = None
        engine.tick(at() + timedelta(seconds=30))
        self.assertEqual(self.repo.get_run(1).entry_average, D("99.5"))
        self.assertEqual(len(self.events("order_status_checked")), 1)

    def test_position_mismatch_records_possible_manual_exit_without_inventing_fill(
        self,
    ):
        self.broker.portfolio.get_positions.return_value = N(status="success", data=[])
        run, verified = self.poll()
        self.assertTrue(
            verified
        )  # Existing management must discover any new GTT exit child first.
        self.assertEqual(run.remaining_quantity, 500)
        event = self.events("position_checked")[0]
        self.assertEqual(
            (event["status"], event["expected_quantity"], event["actual_quantity"]),
            ("mismatch", 500, 0),
        )
        self.assertEqual(len(self.repo.list_intents(1)), 1)

    def test_unknown_status_preserves_uncertainty(self):
        self.raw.status = "validation pending"
        self.raw.filled_quantity = 0
        run, verified = self.poll()
        self.assertFalse(verified)
        self.assertIsNone(run.entry_average)
        self.assertEqual(self.events("order_status_checked")[0]["status"], "unknown")

    def test_empty_intents_do_not_delay_discovery_of_new_order(self):
        self.repo.intents.clear()
        self.poll()
        self.repo.intents[self.intent.id] = self.intent
        self.repo.intents[self.intent.id].broker_id = "o1"
        self.poll(1)
        self.assertEqual(len(self.events("order_status_checked")), 1)

    def test_partial_entry_manually_flattened_cannot_submit_protective_sell(self):
        run = self.repo.get_run(1)
        self.repo.transition(1, run.version, run.state, {"execution_backend": "orders"})
        self.raw.tag = "localgtttag"
        self.raw.status = "open"
        self.raw.filled_quantity = 250
        self.orders.get_trades_by_order.return_value.data[0].quantity = 250
        self.broker.portfolio.get_positions.return_value = N(status="success", data=[])
        engine = StrategyEngine(
            TradingSettings(
                mode="live",
                client_id="client",
                user_id="user",
                execution_backend="orders",
            ),
            self.repo,
            self.market,
            self.broker,
            MagicMock(spec=["assert_owned"]),
        )
        engine.tick(at())
        self.orders.place_order.assert_not_called()
        self.orders.cancel_order.assert_not_called()
        run = self.repo.get_run(1)
        self.assertEqual(
            (run.state, run.remaining_quantity), ("reconciliation_required", 250)
        )
        self.assertIn("manual exit", run.message)

    def test_live_gtt_active_run_is_not_polled(self):
        run = self.repo.get_run(1)
        self.repo.transition(1, run.version, "gtt_active", {})
        self.assertIn(
            1,
            [
                candidate.id
                for candidate in self.repo.list_unresolved(
                    "client", "user", "live"
                )
            ],
        )
        engine = StrategyEngine(
            TradingSettings(mode="live", client_id="client", user_id="user"),
            self.repo,
            self.market,
            self.broker,
            MagicMock(spec=["assert_owned"]),
        )
        engine.tick(at())
        self.assertEqual(self.orders.get_order_status.call_count, 0)
        self.assertEqual(self.repo.get_run(1).state, "gtt_active")

    def test_gtt_controlled_exit_still_requires_its_submitted_tag(self):
        run, _ = self.poll()
        req = OrderRequest("ownedexit", "option", "D", "SELL", "LIMIT", 500, D(98))
        intent = self.repo.create_intent(1, run.version, "exit", req)
        self.repo.intents[intent.id].broker_id = "exit1"
        exit_raw = sdk.OrderBookData(
            order_id="exit1",
            tag="different-tag",
            instrument_token="option",
            product="D",
            transaction_type="SELL",
            quantity=500,
            filled_quantity=0,
            status="open",
        )
        self.orders.get_order_status.side_effect = lambda **kw: (
            sdk.GetOrderDetailsResponse(
                status="success",
                data=exit_raw if kw["order_id"] == "exit1" else self.raw,
            )
        )
        _, verified = self.poll(30)
        self.assertFalse(verified)
        self.assertEqual(self.repo.list_intents(1)[1].status, "unknown")
        self.assertEqual(
            self.events("order_status_unavailable")[0]["order_id"], "exit1"
        )
