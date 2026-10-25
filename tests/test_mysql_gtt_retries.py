"""Opt-in transaction/race checks. Run against a disposable MySQL instance."""

import os
import secrets
import unittest
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal as D

from app.repositories.database import get_connection
from app.repositories.gtt import GttRepository
from app.repositories.strategy import StrategyRepository
from app.strategies.gtt_models import GttLeg, GttSnapshot
from app.strategies.models import BrokerOrder, Fill, OrderRequest, RunKey
from tests import test_mysql_strategy_history as history_tests
from tests.test_strategy_rules import contract


@unittest.skipUnless(
    os.environ.get("ORB_MYSQL_TESTS") == "1",
    "Set ORB_MYSQL_TESTS=1 for disposable MySQL integration",
)
class MySQLGttRetryTests(unittest.TestCase):
    def setUp(self):
        self.client = "test-retry-" + secrets.token_hex(12)
        self.repository = StrategyRepository()
        self.gtts = GttRepository()
        self.addCleanup(lambda: history_tests.MySQLStrategyHistoryTests.cleanup(self))
        key = RunKey(
            self.client, "fixture", "reliance_orb", "paper", datetime.now(UTC).date()
        )
        self.run = self.repository.get_or_create(key)
        self.run = self.repository.transition(
            self.run.id, self.run.version, "waiting_signal", {"contract": contract()}
        )
        self.request = self.request_at(100)
        self.gtts.reserve(self.run, self.request, D(95), D(110))
        self.gtts.acknowledge(self.run.id, "GTT-" + secrets.token_hex(12))

    def request_at(self, price):
        return OrderRequest(
            "retry" + secrets.token_hex(6), "option", "D", "BUY", "LIMIT", 500, D(price)
        )

    def cancel_attempt(self):
        record = self.gtts.get(self.run.id)
        oid = "order-" + secrets.token_hex(12)
        entry = GttLeg("ENTRY", "OPEN", record.request.price, "BUY", oid)
        snapshot = GttSnapshot(
            record.broker_id,
            (
                entry,
                GttLeg("TARGET", "SCHEDULED", record.target, "SELL"),
                GttLeg("STOPLOSS", "SCHEDULED", record.stop, "SELL"),
            ),
        )
        self.gtts.observe(self.run.id, snapshot)
        intent = self.gtts.attach_child(self.run.id, entry, record.request)
        run = self.repository.apply_order(
            intent.id, BrokerOrder(oid, record.request.tag, "open", 500, 0)
        )
        self.gtts.authorize_retry(run, self.gtts.get(run.id))
        mutation = self.gtts.mutation(run.id, "cancel", {})
        snapshot = replace(
            snapshot,
            legs=tuple(replace(leg, status="CANCELLED") for leg in snapshot.legs),
        )
        self.gtts.observe(run.id, snapshot)
        run = self.repository.apply_order(
            intent.id, BrokerOrder(oid, record.request.tag, "cancelled", 500, 0)
        )
        self.gtts.mutation_result(mutation, "confirmed")
        return run, self.gtts.get(run.id), intent

    def test_attempt_archive_keeps_parent_journals_and_distinct_children(self):
        run, previous, old = self.cancel_attempt()
        new = self.gtts.reserve_retry(
            run, previous, self.request_at(102), D(97), D(112)
        )
        self.assertEqual((new.attempt, new.pending["kind"]), (2, "create"))
        self.gtts.acknowledge(run.id, "GTT-" + secrets.token_hex(12))
        record = self.gtts.get(run.id)
        leg = GttLeg(
            "ENTRY", "COMPLETED", D(102), "BUY", "order-" + secrets.token_hex(12)
        )
        intent = self.gtts.attach_child(run.id, leg, record.request)
        self.assertNotEqual(intent.id, old.id)
        filled = self.repository.apply_order(
            intent.id,
            BrokerOrder(
                leg.order_id,
                record.request.tag,
                "complete",
                500,
                500,
                (Fill("trade-" + secrets.token_hex(12), leg.order_id, 500, D(102)),),
            ),
        )
        self.assertEqual(
            (filled.filled_quantity, filled.remaining_quantity, filled.entry_average),
            (500, 500, D(102)),
        )
        conn = get_connection()
        try:
            with conn.cursor() as c:
                c.execute(
                    "SELECT attempt,broker_id FROM strategy_gtt_attempt_history WHERE run_id=%s",
                    (run.id,),
                )
                self.assertEqual(c.fetchall(), ((1, previous.broker_id),))
                c.execute(
                    "SELECT attempt FROM strategy_gtt_legs WHERE run_id=%s ORDER BY attempt",
                    (run.id,),
                )
                self.assertEqual(c.fetchall(), ((1,), (2,)))
                c.execute(
                    "SELECT attempt,kind,status FROM strategy_gtt_mutations WHERE run_id=%s ORDER BY id",
                    (run.id,),
                )
                self.assertEqual(
                    c.fetchall(),
                    (
                        (1, "create", "confirmed"),
                        (1, "cancel", "confirmed"),
                        (2, "create", "confirmed"),
                    ),
                )
        finally:
            conn.close()

    def test_concurrent_retry_reservations_only_one_wins(self):
        run, record, _ = self.cancel_attempt()

        def reserve(_):
            return GttRepository().reserve_retry(
                run, record, self.request_at(102), D(97), D(112)
            )

        with ThreadPoolExecutor(max_workers=2) as pool:
            attempts = list(pool.map(reserve, range(2)))
        self.assertEqual(sum(a is not None for a in attempts), 1)
        self.assertEqual(self.gtts.get(run.id).attempt, 2)

    def test_fill_arriving_after_cancellation_prevents_atomic_retry(self):
        run, record, intent = self.cancel_attempt()
        run = self.repository.apply_order(
            intent.id,
            BrokerOrder(
                intent.broker_id,
                intent.request.tag,
                "complete",
                500,
                500,
                (
                    Fill(
                        "late-trade-" + secrets.token_hex(6),
                        intent.broker_id,
                        500,
                        D(100),
                    ),
                ),
            ),
        )
        with self.assertRaises(ValueError):
            self.gtts.reserve_retry(run, record, self.request_at(102), D(97), D(112))
        self.assertEqual(self.gtts.get(run.id).attempt, 1)

    def test_attempt_budget_and_pending_creation_survive_repository_recreation(self):
        for expected in (2, 3):
            run, record, _ = self.cancel_attempt()
            new = GttRepository().reserve_retry(
                run, record, self.request_at(102), D(97), D(112)
            )
            self.assertIsNotNone(new)
            restored = GttRepository().get(run.id)
            self.assertEqual(
                (restored.attempt, restored.pending["kind"]), (expected, "create")
            )
            self.assertIsNone(restored.broker_id)
            self.gtts.acknowledge(run.id, "GTT-" + secrets.token_hex(12))
        run = self.repository.get_run(self.run.id)
        with self.assertRaises(ValueError):
            self.gtts.authorize_retry(run, self.gtts.get(run.id))

    def test_unconfirmed_cancellation_and_reconciliation_state_block_reservation(self):
        run, record, _ = self.cancel_attempt()
        self.gtts.mutation(run.id, "cancel", {})
        with self.assertRaises(ValueError):
            self.gtts.reserve_retry(run, record, self.request_at(102), D(97), D(112))
        self.assertEqual(self.gtts.get(run.id).attempt, 1)

    def test_retry_disqualification_survives_restart_and_run_state_recovery(self):
        run, _record, _ = self.cancel_attempt()
        self.gtts.block_retries(run.id)
        restored = GttRepository().get(run.id)
        self.assertTrue(restored.retry_blocked)
        with self.assertRaises(ValueError):
            GttRepository().reserve_retry(
                run, restored, self.request_at(102), D(97), D(112)
            )
        self.assertEqual(self.gtts.get(run.id).attempt, 1)
