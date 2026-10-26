import unittest
from dataclasses import replace
from decimal import Decimal as D
from unittest.mock import MagicMock

from app.core.trading import TradingSettings
from app.services.paper_broker import PaperBroker
from app.strategies.engine import StrategyEngine
from app.strategies.gtt_models import GttLeg, GttSnapshot
from app.strategies.models import (
    BrokerOrder,
    BrokerPosition,
    Fill,
    OrderRequest,
    RunKey,
)
from tests.gtt_helpers import MemoryGttRepository
from tests.strategy_helpers import FakeMarket, MemoryRepository
from tests.test_strategy_rules import at


class GttExecutionTests(unittest.TestCase):
    def setUp(self):
        from app.strategies.gtt import GttExecution

        self.repo = MemoryRepository()
        self.gtts = MemoryGttRepository(self.repo)
        self.market = FakeMarket()
        self.paper = PaperBroker(self.market.quote)
        self.engine = StrategyEngine(
            TradingSettings(),
            self.repo,
            self.market,
            self.paper,
            MagicMock(spec=["assert_owned"]),
        )
        run = self.repo.get_or_create(
            RunKey("paper", "paper", "reliance_orb", "paper", at().date())
        )
        self.run = self.repo.transition(
            run.id, run.version, "waiting_signal", {"contract": self.market.contract}
        )
        self.request = OrderRequest(
            "entrygtt", "option", "D", "BUY", "LIMIT", 500, D(100)
        )
        self.adapter = MagicMock()
        self.adapter.place.return_value = "GTT-1"
        self.execution = GttExecution(self.engine, self.adapter, self.gtts)
        self.orders = {}
        self.paper.positions = lambda: [
            BrokerPosition(
                "option", "D", self.repo.get_run(self.run.id).remaining_quantity
            )
        ]
        self.adapter.child_order.side_effect = lambda record, leg, request: replace(
            self.orders[leg.order_id], tag=request.tag
        )

    def enter(self):
        return self.execution.enter(self.run, self.request, at())

    def snapshot(self, fill=500, average="100", target=False, terminal=False):
        self.orders["entry"] = BrokerOrder(
            "entry",
            "unused",
            "complete" if fill == 500 else "open",
            500,
            fill,
            (Fill("entrytrade", "entry", fill, D(average)),) if fill else (),
        )
        legs = [
            GttLeg(
                "ENTRY", "COMPLETED" if fill == 500 else "OPEN", D(100), "BUY", "entry"
            )
        ]
        for strategy, price, side in [
            ("TARGET", "108", "SELL"),
            ("STOPLOSS", "95", "SELL"),
        ]:
            status = "CANCELLED" if terminal else "SCHEDULED"
            oid = None
            if target and strategy == "TARGET":
                status = "COMPLETED"
                oid = "target"
                self.orders[oid] = BrokerOrder(
                    oid,
                    "unused",
                    "complete",
                    500,
                    500,
                    (Fill("targettrade", oid, 500, D(108)),),
                )
            legs.append(GttLeg(strategy, status, D(price), side, oid))
        return GttSnapshot("GTT-1", tuple(legs))

    def test_creation_reserved_before_submission_and_never_retried(self):
        def fail(record):
            self.assertTrue(self.repo.get_run(self.run.id).entry_reserved)
            self.assertEqual(self.gtts.get(self.run.id).pending["kind"], "create")
            raise TimeoutError("secret")

        self.adapter.place.side_effect = fail
        with self.assertRaises(TimeoutError):
            self.enter()
        self.execution.manage(self.repo.get_run(self.run.id), at())
        self.assertEqual(self.adapter.place.call_count, 1)
        self.assertEqual(
            self.repo.get_run(self.run.id).state, "reconciliation_required"
        )

    def test_full_fill_uses_actual_average_and_broker_target(self):
        self.enter()
        initial = self.snapshot(average="99.5")
        updated = replace(
            initial,
            legs=tuple(
                replace(
                    l,
                    price=D("107.5")
                    if l.strategy == "TARGET"
                    else D("94.5")
                    if l.strategy == "STOPLOSS"
                    else l.price,
                )
                for l in initial.legs
            ),
        )
        self.adapter.snapshot.side_effect = [initial, updated]
        self.execution.manage(self.repo.get_run(self.run.id), at())
        run = self.repo.get_run(self.run.id)
        self.assertEqual(
            (run.state, run.stop, run.target, run.remaining_quantity),
            ("open", D("94.5"), D("107.5"), 500),
        )
        self.adapter.modify.assert_called_once()
        self.assertFalse(
            any(i.purpose in {"stop", "exit"} for i in self.repo.intents.values())
        )

    def test_manual_flat_position_stays_unresolved_without_another_sell(self):
        self.enter()
        self.adapter.snapshot.return_value = self.snapshot()
        self.paper.positions = list
        self.execution.manage(self.repo.get_run(self.run.id), at(11, 0))
        run = self.repo.get_run(self.run.id)
        self.assertEqual(run.state, "reconciliation_required")
        self.assertIn("manual exit", run.message)
        self.assertEqual(run.remaining_quantity, 500)
        self.assertTrue(self.gtts.get(run.id).retry_blocked)
        self.assertFalse(any(i.purpose == "exit" for i in self.repo.intents.values()))
        self.adapter.modify.assert_not_called()
        self.adapter.cancel.assert_not_called()

    def test_active_target_prevents_local_duplicate_sell(self):
        self.enter()
        self.adapter.snapshot.return_value = self.snapshot()
        self.execution.manage(self.repo.get_run(self.run.id), at())
        self.market.move(at(10, 0), "118", "119")
        self.execution.manage(self.repo.get_run(self.run.id), at(10, 0))
        self.assertEqual(self.repo.get_run(self.run.id).state, "open")
        self.assertFalse(any(i.purpose == "exit" for i in self.repo.intents.values()))

    def test_zero_position_still_waits_for_sibling_cancellation(self):
        self.enter()
        active = self.snapshot(target=True)
        terminal = replace(
            active,
            legs=tuple(
                replace(l, status="CANCELLED") if l.strategy == "STOPLOSS" else l
                for l in active.legs
            ),
        )
        self.adapter.snapshot.side_effect = [active, terminal, terminal]
        self.execution.manage(self.repo.get_run(self.run.id), at())
        self.assertEqual(self.repo.get_run(self.run.id).state, "closed")
        self.adapter.cancel.assert_called_once()
        self.assertFalse(any(i.purpose == "exit" for i in self.repo.intents.values()))

    def test_cancel_uncertain_blocks_timed_exit_and_retry(self):
        self.enter()
        self.adapter.snapshot.return_value = self.snapshot()
        self.adapter.cancel.side_effect = TimeoutError("secret")
        for _ in range(2):
            self.execution.manage(self.repo.get_run(self.run.id), at(15, 25))
        self.assertEqual(self.adapter.cancel.call_count, 1)
        self.assertEqual(
            self.repo.get_run(self.run.id).state, "reconciliation_required"
        )
        self.assertFalse(any(i.purpose == "exit" for i in self.repo.intents.values()))

    def test_partial_fill_cancels_parent_and_exchange_entry_before_flatten(self):
        self.enter()
        active = self.snapshot(fill=250)
        cancelled = replace(
            active, legs=tuple(replace(l, status="CANCELLED") for l in active.legs)
        )
        self.adapter.snapshot.side_effect = [active, cancelled, cancelled, cancelled]
        # Acknowledge a confirmed terminal child after the cancellation request.
        self.paper.cancel = MagicMock(
            side_effect=lambda oid: self.orders.update(
                {oid: replace(self.orders[oid], status="cancelled")}
            )
        )
        self.paper.positions = lambda: [BrokerPosition("option", "D", 250)]
        self.market.move(at(9, 45, 31))
        self.execution.manage(self.repo.get_run(self.run.id), at(9, 45, 31))
        self.paper.cancel.assert_called_once_with("entry")
        exits = [i for i in self.repo.intents.values() if i.purpose == "exit"]
        self.assertEqual(len(exits), 1)
        self.assertEqual(exits[0].request.quantity, 250)

    def test_entry_expiring_during_persistence_is_not_submitted(self):
        self.engine.clock = MagicMock(side_effect=[at(), at(15, 1)])
        self.enter()
        self.adapter.place.assert_not_called()
        self.assertTrue(self.repo.get_run(self.run.id).entry_reserved)
        self.assertEqual(self.repo.get_run(self.run.id).state, "rejected")

    def test_unknown_modify_is_not_repeated_on_recovery(self):
        self.enter()
        self.adapter.snapshot.return_value = self.snapshot(average="99.5")
        self.adapter.modify.side_effect = TimeoutError("secret")
        for _ in range(2):
            self.execution.manage(self.repo.get_run(self.run.id), at())
        self.assertEqual(self.adapter.modify.call_count, 1)
        self.assertEqual(
            self.repo.get_run(self.run.id).state, "reconciliation_required"
        )

    def test_unfilled_entry_times_out_without_retry_when_entries_stopped(self):
        self.enter()
        self.engine.stop_entries()
        active = self.snapshot(fill=0)
        cancelled = replace(
            active, legs=tuple(replace(l, status="CANCELLED") for l in active.legs)
        )
        self.adapter.snapshot.side_effect = [active, cancelled, cancelled, cancelled]
        self.paper.cancel = MagicMock(
            side_effect=lambda oid: self.orders.update(
                {oid: replace(self.orders[oid], status="cancelled")}
            )
        )
        self.execution.manage(self.repo.get_run(self.run.id), at(9, 45, 31))
        self.assertEqual(self.repo.get_run(self.run.id).state, "rejected")
        self.assertTrue(self.repo.get_run(self.run.id).entry_reserved)
        self.assertFalse(any(i.purpose == "exit" for i in self.repo.intents.values()))

    def test_ownership_loss_after_reservation_never_submits(self):
        self.engine.ownership.assert_owned.side_effect = RuntimeError("lost")
        with self.assertRaises(RuntimeError):
            self.enter()
        self.adapter.place.assert_not_called()
        self.assertTrue(self.repo.get_run(self.run.id).entry_reserved)

    def test_inactive_exits_are_not_reported_as_protected(self):
        self.enter()
        active = self.snapshot()
        unprotected = replace(
            active,
            legs=tuple(
                replace(l, status="INACTIVE") if l.strategy != "ENTRY" else l
                for l in active.legs
            ),
        )
        self.adapter.snapshot.return_value = unprotected
        self.adapter.cancel.side_effect = TimeoutError("uncertain")
        self.execution.manage(self.repo.get_run(self.run.id), at())
        self.assertEqual(
            self.repo.get_run(self.run.id).state, "reconciliation_required"
        )
        self.adapter.cancel.assert_called_once()

    def test_unknown_modify_still_allows_confirmed_timed_exit(self):
        self.enter()
        active = self.snapshot(average="99.5")
        terminal = replace(
            active,
            legs=tuple(
                replace(leg, status="CANCELLED") if leg.strategy != "ENTRY" else leg
                for leg in active.legs
            ),
        )
        self.adapter.snapshot.return_value = active
        self.adapter.modify.side_effect = TimeoutError("uncertain")
        self.execution.manage(self.repo.get_run(self.run.id), at())
        self.assertEqual(
            self.repo.get_run(self.run.id).state, "reconciliation_required"
        )
        self.adapter.snapshot.side_effect = [active, terminal, terminal]
        self.market.move(at(15, 25))
        self.execution.manage(self.repo.get_run(self.run.id), at(15, 25))
        self.adapter.modify.assert_called_once()
        self.adapter.cancel.assert_called_once()
        exits = [i for i in self.repo.intents.values() if i.purpose == "exit"]
        self.assertEqual(len(exits), 1)
        self.assertEqual(exits[0].request.quantity, 500)

    def test_unknown_modify_does_not_block_broker_exit_cleanup(self):
        self.enter()
        active = self.snapshot(average="99.5")
        self.adapter.snapshot.return_value = active
        self.adapter.modify.side_effect = ValueError("rejected")
        self.execution.manage(self.repo.get_run(self.run.id), at())
        filled = self.snapshot(average="99.5", target=True)
        terminal = replace(
            filled,
            legs=tuple(
                replace(leg, status="CANCELLED") if leg.strategy == "STOPLOSS" else leg
                for leg in filled.legs
            ),
        )
        self.adapter.snapshot.side_effect = [filled, terminal, terminal]
        self.execution.manage(self.repo.get_run(self.run.id), at())
        self.assertEqual(
            (
                self.repo.get_run(self.run.id).state,
                self.repo.get_run(self.run.id).remaining_quantity,
            ),
            ("closed", 0),
        )
        self.adapter.cancel.assert_called_once()
        self.assertFalse(any(i.purpose == "exit" for i in self.repo.intents.values()))


class GttIntegrationTests(unittest.TestCase):
    def setup_engine(self, direction="CE"):
        from app.services.paper_gtt import PaperGttBroker

        self.repo = MemoryRepository()
        self.gtts = MemoryGttRepository(self.repo)
        self.market = FakeMarket(direction)
        self.paper = PaperBroker(self.market.quote, clock=lambda: self.market.now)
        self.adapter = PaperGttBroker(self.paper)
        self.lock = MagicMock(spec=["assert_owned"])
        self.engine = StrategyEngine(
            TradingSettings(),
            self.repo,
            self.market,
            self.paper,
            self.lock,
            gtt_broker=self.adapter,
            gtt_repository=self.gtts,
        )

    def latest(self):
        return self.repo.get_run(1)

    def tick(self, now=None):
        self.engine.tick(now or self.market.now)

    def test_call_and_put_gtt_target_and_no_reentry(self):
        for direction in ["CE", "PE"]:
            with self.subTest(direction=direction):
                self.setup_engine(direction)
                self.tick()
                self.tick()
                run = self.latest()
                self.assertEqual(
                    (
                        run.execution_backend,
                        run.stop,
                        run.target,
                        run.remaining_quantity,
                    ),
                    ("gtt", D(95), D(108), 500),
                )
                self.assertEqual(run.contract.option_type, direction)
                self.market.move(at(10, 0), "110", "111")
                self.tick()
                self.tick()
                self.tick()
                self.assertEqual(
                    (self.latest().state, self.latest().remaining_quantity),
                    ("closed", 0),
                )
                self.assertEqual(
                    sum(i.purpose == "entry" for i in self.repo.intents.values()), 1
                )
                self.assertEqual(len(self.gtts.records), 1)

    def test_expiry_day_does_not_reserve_or_create_a_gtt(self):
        for direction in ("CE", "PE"):
            with self.subTest(direction=direction):
                self.setup_engine(direction)
                self.market.contract = replace(self.market.contract, expiry=at().date())
                self.tick()
                self.assertEqual(self.latest().state, "skipped")
                self.assertFalse(self.latest().entry_reserved)
                self.assertFalse(self.gtts.records)
                self.assertFalse(self.repo.intents)

    def test_restricted_expiry_places_gtt_only_for_next_month(self):
        from datetime import timedelta

        for direction in ("CE", "PE"):
            with self.subTest(direction=direction):
                self.setup_engine(direction)
                expiring = replace(self.market.contract, expiry=at().date())
                later = replace(expiring, instrument_key="next-month",
                                expiry=at().date() + timedelta(days=30))
                self.market.q = replace(self.market.q, instrument_key="next-month")
                snapshot = replace(self.market.snapshot(at()), contracts=[expiring, later])
                self.market.snapshot = lambda now, contract=None, snapshot=snapshot: snapshot
                self.tick()
                self.assertEqual(self.latest().contract, later)
                self.assertTrue(self.latest().entry_reserved)
                self.assertEqual(self.gtts.get(self.latest().id).request.instrument,
                                 "next-month")

    def test_restart_and_backend_setting_change_keep_gtt_management(self):
        from app.services.paper_gtt import PaperGttBroker

        self.setup_engine()
        self.tick()
        self.tick()
        self.paper = PaperBroker(self.market.quote, clock=lambda: self.market.now)
        self.adapter = PaperGttBroker(self.paper)
        self.engine = StrategyEngine(
            TradingSettings(execution_backend="orders"),
            self.repo,
            self.market,
            self.paper,
            self.lock,
            gtt_broker=self.adapter,
            gtt_repository=self.gtts,
        )
        self.market.move(at(15, 25))
        self.tick()
        self.tick()
        self.tick()
        self.assertEqual(
            (self.latest().state, self.latest().remaining_quantity), ("closed", 0)
        )

    def test_stale_quotes_do_not_fill_paper_gtt(self):
        self.setup_engine()
        self.tick()
        self.market.now = at(9, 45, 10)
        self.tick()
        self.assertEqual(
            (self.latest().state, self.latest().remaining_quantity), ("open", 500)
        )

    def test_stop_is_triggered_by_gtt_without_local_stop_order(self):
        self.setup_engine()
        self.tick()
        self.tick()
        self.market.move(at(10, 0), "95", "96")
        self.tick()
        self.tick()
        self.assertEqual(
            (self.latest().state, self.latest().remaining_quantity), ("closed", 0)
        )
        self.assertEqual(
            sum(i.purpose == "stop" for i in self.repo.intents.values()), 1
        )
        self.assertFalse(any(i.purpose == "exit" for i in self.repo.intents.values()))
