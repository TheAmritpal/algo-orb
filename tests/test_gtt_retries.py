"""Entry retry behavior with real paper execution and durable repository doubles."""

import unittest
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal as D
from unittest.mock import MagicMock

from app.core.trading import TradingSettings
from app.services.paper_broker import PaperBroker
from app.services.paper_gtt import PaperGttBroker
from app.strategies.engine import StrategyEngine
from app.strategies.models import BrokerPosition, EntryLimits, Fill, Quote
from tests.gtt_helpers import MemoryGttRepository
from tests.strategy_helpers import FakeMarket, MemoryRepository
from tests.test_strategy_rules import at


class GttRetryTests(unittest.TestCase):
    def setUp(self):
        self.repo = MemoryRepository()
        self.gtts = MemoryGttRepository(self.repo)
        self.market = FakeMarket()
        self.gtts.clock = lambda: self.market.now
        self.paper = PaperBroker(self.market.quote, clock=lambda: self.market.now)
        self.adapter = PaperGttBroker(self.paper)
        self.engine = self.make_engine()
        # Ask rises between validation and broker submission: entry stays open.
        original = self.adapter.place

        def place(record):
            self.market.move(self.market.now, "101", "102")
            return original(record)

        self.adapter.place = place
        self.engine.tick(self.market.now)
        self.assertEqual(self.latest().filled_quantity, 0)

    def make_engine(self):
        return StrategyEngine(
            TradingSettings(),
            self.repo,
            self.market,
            self.paper,
            MagicMock(spec=["assert_owned"]),
            clock=lambda: self.market.now,
            gtt_broker=self.adapter,
            gtt_repository=self.gtts,
        )

    def latest(self):
        return self.repo.get_run(1)

    def tick(self, seconds=0):
        self.market.move(self.market.now + timedelta(seconds=seconds), "101", "102")
        self.engine.tick(self.market.now)

    def test_timeout_retries_at_fresh_ask_with_new_levels_and_preserves_old_order(self):
        self.tick(30)
        record = self.gtts.get(1)
        self.assertEqual(record.request.price, D(102))
        self.assertEqual((record.stop, record.target), (D(97), D(112)))
        self.assertEqual(record.attempt, 2)
        self.assertEqual(len(self.adapter.parents), 2)
        self.assertTrue(self.gtts.history[(1, 1)].snapshot.terminal)
        old = self.repo.list_intents(1)
        self.assertEqual(len(old), 1)
        self.assertEqual((old[0].status, old[0].fills), ("cancelled", []))
        self.tick()
        self.assertEqual(self.latest().filled_quantity, 500)
        self.assertEqual(self.latest().entry_average, D(102))

    def test_no_retry_before_thirty_seconds(self):
        self.tick(29)
        self.assertEqual(len(self.adapter.parents), 1)
        self.assertEqual(self.latest().state, "entry_pending")

    def test_restricted_expiry_blocks_retry_without_rolling_original_contract(self):
        expiry = self.latest().contract.expiry
        self.market.next_trading_session = lambda now: expiry
        self.tick(30)
        self.assertEqual(len(self.adapter.parents), 1)
        self.assertEqual(self.latest().state, "rejected")
        self.assertIn("expiry_restricted", self.latest().message)


    def test_three_attempt_budget_survives_restart_and_never_submits_fourth(self):
        original = PaperGttBroker.place

        def never_fill(record):
            self.market.move(
                self.market.now,
                str(record.request.price + 1),
                str(record.request.price + 2),
            )
            return original(self.adapter, record)

        self.adapter.place = never_fill
        for ask in (110, 120):
            self.market.move(
                self.market.now + timedelta(seconds=30), str(ask - 1), str(ask)
            )
            self.engine.tick(self.market.now)
            self.engine = self.make_engine()
        self.assertEqual(self.gtts.get(1).attempt, 3)
        self.market.move(self.market.now + timedelta(seconds=30), "129", "130")
        self.engine.tick(self.market.now)
        self.assertEqual(len(self.adapter.parents), 3)
        self.assertEqual(self.latest().state, "rejected")
        self.tick(30)
        self.assertEqual(len(self.adapter.parents), 3)

    def test_cancel_fill_race_prevents_new_entry_and_flattens(self):
        original = self.adapter.cancel

        def fill_then_cancel(record):
            self.market.move(self.market.now, "98", "99")
            original(record)

        self.adapter.cancel = fill_then_cancel
        self.tick(30)
        self.assertEqual(len(self.adapter.parents), 1)
        self.tick()
        self.assertEqual(self.latest().remaining_quantity, 0)
        self.assertEqual(self.latest().filled_quantity, 500)

    def test_uncertain_cancel_never_resubmits(self):
        self.adapter.cancel = MagicMock(side_effect=TimeoutError("redact"))
        self.tick(30)
        self.tick(30)
        self.assertEqual(len(self.adapter.parents), 1)
        self.assertEqual(self.latest().state, "reconciliation_required")

    def test_uncertainty_before_timeout_permanently_blocks_retries_after_recovery(self):
        original = self.adapter.snapshot
        self.adapter.snapshot = MagicMock(side_effect=TimeoutError("redact"))
        self.tick(1)
        self.assertEqual(self.latest().state, "reconciliation_required")
        self.adapter.snapshot = original
        self.engine = self.make_engine()
        self.tick(1)
        self.tick(28)
        self.assertEqual(len(self.adapter.parents), 1)
        self.assertEqual(self.gtts.get(1).attempt, 1)

    def test_uncertain_retry_creation_never_resubmits(self):
        self.adapter.place = MagicMock(side_effect=TimeoutError("redact"))
        self.tick(30)
        self.assertEqual(self.gtts.get(1).attempt, 2)
        self.assertIsNone(self.gtts.get(1).broker_id)
        self.assertEqual(self.latest().state, "reconciliation_required")
        self.tick(30)
        self.assertEqual(self.gtts.get(1).attempt, 2)
        self.assertEqual(self.adapter.place.call_count, 1)

    def test_risk_checks_prevent_retry(self):
        self.engine.settings = replace(
            self.engine.settings,
            limits=EntryLimits(D(101), D(999999), D(5000), D(1), D(1)),
        )
        self.tick(30)
        self.assertEqual(len(self.adapter.parents), 1)
        self.assertEqual(self.latest().state, "rejected")

    def test_insufficient_funds_prevents_retry(self):
        self.paper.available_funds = lambda: D(1)
        self.tick(30)
        self.assertEqual(len(self.adapter.parents), 1)
        self.assertEqual(self.latest().state, "rejected")

    def test_external_position_prevents_retry(self):
        self.paper.positions = lambda: [BrokerPosition("option", "D", 500)]
        self.tick(30)
        self.assertEqual(len(self.adapter.parents), 1)
        self.assertEqual(self.latest().state, "reconciliation_required")

    def test_same_contract_intraday_position_prevents_retry(self):
        self.paper.positions = lambda: [BrokerPosition("option", "I", 500)]
        self.tick(30)
        self.assertEqual(len(self.adapter.parents), 1)
        self.assertEqual(self.latest().state, "rejected")

    def test_partial_fill_is_flattened_without_retry(self):
        oid = next(iter(self.paper.orders))
        self.paper.orders[oid] = replace(
            self.paper.orders[oid],
            filled_quantity=250,
            fills=(Fill("partial", oid, 250, D(100)),),
        )
        self.tick(30)
        self.tick()
        self.assertEqual(len(self.adapter.parents), 1)
        self.assertEqual(self.latest().filled_quantity, 250)
        self.assertEqual(self.latest().remaining_quantity, 0)

    def test_cutoff_or_date_rollover_never_retries(self):
        for now in [at(15, 0, 1), at() + timedelta(days=1)]:
            with self.subTest(now=now):
                self.setUp()
                self.market.move(now, "101", "102")
                self.engine.tick(now)
                self.assertEqual(len(self.adapter.parents), 1)
                self.assertEqual(self.latest().state, "rejected")

    def test_stopped_entries_never_retries(self):
        self.engine.stop_entries()
        self.tick(30)
        self.assertEqual(len(self.adapter.parents), 1)

    def test_stale_quote_prevents_retry(self):
        stale = Quote("option", at(), D(101), D(102), D(101))
        self.market.quote = lambda instrument: stale
        self.tick(30)
        self.assertEqual(len(self.adapter.parents), 1)
        self.assertEqual(self.latest().state, "rejected")

    def test_quote_expiring_during_retry_reservation_is_never_submitted(self):
        original = self.gtts.reserve_retry

        def reserve(*args):
            record = original(*args)
            self.market.now += timedelta(seconds=2)
            return record

        self.gtts.reserve_retry = reserve
        self.market.move(at(9, 45, 30), "101", "102")
        recent = replace(self.market.q, timestamp=at(9, 45, 26))
        self.market.quote = lambda instrument: recent
        self.engine.tick(self.market.now)
        self.assertEqual(len(self.adapter.parents), 1)
        self.assertEqual(self.latest().state, "rejected")
        self.assertEqual(self.gtts.get(1).pending, None)

    def test_cutoff_reached_during_retry_reservation_is_never_submitted(self):
        original = self.gtts.reserve_retry

        def reserve(*args):
            record = original(*args)
            self.market.now = at(15, 0)
            return record

        self.gtts.reserve_retry = reserve
        self.market.move(at(14, 59, 59), "101", "102")
        self.engine.tick(self.market.now)
        self.assertEqual(len(self.adapter.parents), 1)
        self.assertEqual(self.latest().state, "rejected")

    def test_restart_after_confirmed_cancellation_resumes_authorized_retry(self):
        # Simulate a process death after draining, before reserving the retry.
        self.engine.gtt._retry_entry = lambda run, record, now: run
        self.tick(30)
        self.assertTrue(self.gtts.get(1).retry_requested)
        self.assertTrue(self.gtts.get(1).snapshot.terminal)
        self.engine = self.make_engine()
        self.tick()
        self.assertEqual(self.gtts.get(1).attempt, 2)
        self.assertEqual(len(self.adapter.parents), 2)

    def test_confirmed_broker_rejection_does_not_trigger_retry(self):
        oid = next(iter(self.paper.orders))
        self.paper.orders[oid] = replace(self.paper.orders[oid], status="rejected")
        self.tick(30)
        self.assertEqual(len(self.adapter.parents), 1)
        self.assertEqual(self.gtts.get(1).attempt, 1)
        self.assertFalse(self.gtts.get(1).retry_requested)

    def test_closed_session_prevents_retry(self):
        original = self.market.snapshot
        self.market.snapshot = lambda now, contract=None: replace(
            original(now, contract), normal_session=False
        )
        self.tick(30)
        self.assertEqual(len(self.adapter.parents), 1)
        self.assertEqual(self.latest().state, "rejected")
