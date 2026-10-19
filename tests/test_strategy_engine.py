import unittest
from decimal import Decimal as D
from unittest.mock import MagicMock

from app.core.trading import TradingSettings
from app.services.paper_broker import PaperBroker
from app.services.trading_market import TradingUnavailable
from app.strategies.engine import StrategyEngine
from app.strategies.models import BrokerOrder, BrokerPosition, Fill
from tests.strategy_helpers import FakeMarket, MemoryRepository
from tests.test_strategy_rules import at


class EngineTests(unittest.TestCase):
    def setup_engine(self, direction="CE", broker=None):
        self.repo = MemoryRepository()
        self.market = FakeMarket(direction)
        self.lock = MagicMock(spec=["assert_owned"])
        self.broker = broker or PaperBroker(self.market.quote)
        self.engine = StrategyEngine(
            TradingSettings(execution_backend="orders"),
            self.repo,
            self.market,
            self.broker,
            self.lock,
        )

    def run_now(self, now=None):
        self.engine.tick(now or at())

    def latest(self):
        return list(self.repo.runs.values())[-1]

    def test_call_and_put_buy_two_lots_D_and_fill_based_levels(self):
        for direction in ("CE", "PE"):
            with self.subTest(direction=direction):
                self.setup_engine(direction)
                self.run_now()
                self.run_now()
                r = self.latest()
                entry = self.repo.list_intents(r.id)[0]
                self.assertEqual(
                    (
                        entry.request.product,
                        entry.request.quantity,
                        r.contract.option_type,
                    ),
                    ("D", 500, direction),
                )
                self.assertEqual(
                    (r.entry_average, r.stop, r.target), (D(100), D(90), D(118))
                )
                self.assertEqual(r.remaining_quantity, 500)

    def test_target_closes_and_daily_limit_prevents_reentry(self):
        self.setup_engine()
        self.run_now()
        self.run_now()
        self.market.move(at(10, 0), "120", "121")
        for _ in range(3):
            self.run_now(at(10, 0))
        self.assertEqual(
            (self.latest().state, self.latest().remaining_quantity), ("closed", 0)
        )
        self.assertEqual(
            sum(i.purpose == "entry" for i in self.repo.intents.values()), 1
        )

    def test_timed_exit_on_restart_and_entry_disable_still_manages_position(self):
        self.setup_engine()
        self.run_now()
        self.run_now()
        self.market.move(at(15, 26))
        self.broker = PaperBroker(self.market.quote)
        self.engine = StrategyEngine(
            TradingSettings(execution_backend="orders"),
            self.repo,
            self.market,
            self.broker,
            self.lock,
        )
        self.engine.stop_entries()
        for _ in range(3):
            self.run_now(at(15, 26))
        self.assertEqual(
            (self.latest().state, self.latest().remaining_quantity), ("closed", 0)
        )

    def test_cancel_race_stop_fills_before_target_exit_no_oversell(self):
        self.setup_engine()
        self.run_now()
        self.run_now()
        original = self.broker.cancel

        def cancel(oid):
            self.market.move(at(10, 0), "90", "91")
            return original(oid)

        self.broker.cancel = cancel
        self.market.move(at(10, 0), "120", "121")
        for _ in range(3):
            self.run_now(at(10, 0))
        sells = sum(
            sum(f.quantity for f in i.fills)
            for i in self.repo.intents.values()
            if i.purpose != "entry"
        )
        self.assertEqual(sells, 500)
        self.assertEqual(self.latest().remaining_quantity, 0)

    def test_cancel_timeout_preserves_exposure_and_never_adds_second_sell(self):
        self.setup_engine()
        self.run_now()
        self.run_now()
        self.market.move(at(15, 25))
        self.broker.cancel = MagicMock(side_effect=TradingUnavailable("uncertain"))
        self.run_now(at(15, 25))
        self.run_now(at(15, 25))
        self.assertEqual(self.latest().state, "reconciliation_required")
        self.assertEqual(
            sum(i.purpose == "exit" for i in self.repo.intents.values()), 0
        )

    def test_unknown_entry_never_retries(self):
        self.setup_engine()
        self.broker.submit = MagicMock(side_effect=TradingUnavailable("uncertain"))
        self.run_now()
        self.run_now()
        self.assertEqual(self.broker.submit.call_count, 1)
        self.assertEqual(self.latest().state, "reconciliation_required")

    def test_manual_same_contract_blocks_entry_unrelated_positions_preserved(self):
        self.setup_engine()
        self.broker.positions = lambda: [
            BrokerPosition("option", "D", 250),
            BrokerPosition("other", "D", 100),
        ]
        self.run_now()
        self.assertEqual(len(self.repo.intents), 0)
        self.assertEqual(self.latest().state, "skipped")

    def test_lock_loss_before_submission_persists_unknown_intent(self):
        self.setup_engine()
        self.lock.assert_owned.side_effect = RuntimeError("ownership lost")
        self.run_now()
        self.assertEqual(len(self.broker.orders), 0)
        self.assertEqual(self.latest().state, "reconciliation_required")

    def test_stale_quote_at_deadline_does_not_claim_closed(self):
        self.setup_engine()
        self.run_now()
        self.run_now()
        self.run_now(at(15, 25))
        self.assertEqual(self.latest().remaining_quantity, 500)
        self.assertEqual(self.latest().state, "reconciliation_required")

    def test_partial_entry_is_protected_and_remainder_cancelled_without_topup(self):
        class Partial(PaperBroker):
            def _evaluate(self, order):
                req = self.requests[order.broker_id]
                if req.side == "BUY" and order.status not in {"cancelled", "rejected"}:
                    result = BrokerOrder(
                        order.broker_id,
                        req.tag,
                        "open",
                        500,
                        250,
                        (Fill("partial", order.broker_id, 250, D(100)),),
                    )
                    self.orders[order.broker_id] = result
                    return result
                return super()._evaluate(order)

        self.setup_engine()
        self.broker = Partial(self.market.quote)
        self.engine = StrategyEngine(
            TradingSettings(execution_backend="orders"),
            self.repo,
            self.market,
            self.broker,
            self.lock,
        )
        self.run_now()
        stops = [i for i in self.repo.intents.values() if i.purpose == "stop"]
        self.assertEqual(stops[0].request.quantity, 250)
        self.market.move(at(9, 46))
        self.run_now(at(9, 46))
        self.run_now(at(9, 46))
        entries = [i for i in self.repo.intents.values() if i.purpose == "entry"]
        self.assertEqual(
            (len(entries), entries[0].status, self.latest().remaining_quantity),
            (1, "cancelled", 250),
        )

    def test_rejected_entry_consumes_daily_allowance(self):
        self.setup_engine()
        original = self.broker.submit

        def reject(request):
            order = original(request)
            result = BrokerOrder(
                order.broker_id, order.tag, "rejected", request.quantity, 0
            )
            self.broker.orders[order.broker_id] = result
            return result

        self.broker.submit = reject
        for _ in range(3):
            self.run_now()
        self.assertEqual(self.latest().state, "rejected")
        self.assertEqual(len(self.repo.intents), 1)

    def test_rejected_stop_immediately_exits_known_position(self):
        self.setup_engine()
        original = self.broker.submit

        def reject_stop(request):
            result = original(request)
            if request.order_type == "SL":
                result = BrokerOrder(
                    result.broker_id, result.tag, "rejected", request.quantity, 0
                )
                self.broker.orders[result.broker_id] = result
            return result

        self.broker.submit = reject_stop
        self.run_now()
        self.run_now()
        self.assertEqual(
            (self.latest().state, self.latest().remaining_quantity), ("closed", 0)
        )

    def test_manual_position_change_halts_automatic_sells(self):
        self.setup_engine()
        self.run_now()
        self.run_now()
        self.broker.positions = lambda: [
            BrokerPosition("option", "D", 250),
            BrokerPosition("other", "D", 100),
        ]
        self.market.move(at(15, 25))
        self.run_now(at(15, 25))
        self.assertEqual(self.latest().state, "reconciliation_required")
        self.assertFalse(any(i.purpose == "exit" for i in self.repo.intents.values()))

    def test_historical_recorded_levels_survive_recovery(self):
        self.setup_engine()
        self.run_now()
        self.run_now()
        run = self.latest()
        self.repo.transition(run.id, run.version, "open", {"target": D(120)})
        self.run_now()
        self.assertEqual(self.latest().target, D(120))
