"""Opt-in persistence checks; fake paper records never call a broker."""

import os
import secrets
import unittest
from datetime import UTC, datetime
from decimal import Decimal as D

from app.repositories.database import get_connection
from app.repositories.gtt import GttRepository
from app.repositories.strategy import StrategyRepository
from app.strategies.gtt_models import GttLeg, GttSnapshot
from app.strategies.models import BrokerOrder, Fill, OpeningRange, OrderRequest, RunKey
from app.strategies.rules import build_range, evaluate_breakout
from tests.test_strategy_rules import at, candle, contract


@unittest.skipUnless(
    os.environ.get("ORB_MYSQL_TESTS") == "1",
    "Set ORB_MYSQL_TESTS=1 for MySQL integration",
)
class MySQLStrategyHistoryTests(unittest.TestCase):
    def setUp(self):
        self.client = "test-history-" + secrets.token_hex(12)
        self.repository = StrategyRepository()
        self.addCleanup(self.cleanup)

    def cleanup(self):
        conn = get_connection()
        try:
            with conn.cursor() as c:
                c.execute(
                    "SELECT id FROM strategy_runs WHERE client_id=%s", (self.client,)
                )
                for (run_id,) in c.fetchall():
                    for table in (
                        "strategy_gtt_attempt_history",
                        "strategy_signals",
                        "strategy_gtt_legs",
                        "strategy_gtt_mutations",
                        "strategy_gtts",
                        "strategy_events",
                    ):
                        c.execute(
                            "DELETE FROM " + table + " WHERE run_id=%s", (run_id,)
                        )
                    c.execute(
                        "DELETE f FROM strategy_fills f JOIN strategy_orders o ON f.intent_id=o.id WHERE o.run_id=%s",
                        (run_id,),
                    )
                    c.execute("DELETE FROM strategy_orders WHERE run_id=%s", (run_id,))
                    c.execute("DELETE FROM strategy_runs WHERE id=%s", (run_id,))
            conn.commit()
        finally:
            conn.close()

    def run_record(self):
        key = RunKey(
            self.client, "fixture", "reliance_orb", "paper", datetime.now(UTC).date()
        )
        return self.repository.get_or_create(key)

    def test_decisions_are_deduplicated_and_history_is_account_and_mode_scoped(self):
        self.assertTrue(hasattr(self.repository, "record_decision"))
        run = self.run_record()
        candles = [candle(), candle(30, "1489")]
        decision = evaluate_breakout(candles, build_range(candles, at()), at(), None)
        self.assertTrue(self.repository.record_decision(run.id, decision, at()))
        self.assertFalse(self.repository.record_decision(run.id, decision, at()))
        self.repository.append_event(
            run.id,
            "entry_blocked",
            {"reason": "protection_unverified", "access_token": "never-store-this"},
        )
        decisions, orders, events = self.repository.history_details(run.id)
        self.assertEqual(len(decisions), 1)
        self.assertEqual(
            (decisions[0]["direction"], decisions[0]["close_price"]), ("PE", D(1489))
        )
        self.assertEqual(decisions[0]["candle_close"].utcoffset().total_seconds(), 0)
        self.assertEqual(events[0]["fields"], {"reason": "protection_unverified"})
        self.assertEqual(orders, [])
        self.assertEqual(
            [
                r.id
                for r in self.repository.history_runs(self.client, "fixture", "paper")
            ],
            [run.id],
        )
        self.assertEqual(
            self.repository.history_runs(self.client, "other-account", "paper"), []
        )
        self.assertEqual(
            self.repository.history_runs(self.client, "fixture", "live"), []
        )
        self.assertEqual(
            self.repository.history_runs(self.client, "fixture", "paper", page=2), []
        )

    def test_gtt_reservation_snapshots_fills_and_recovery_journal_round_trip(self):
        run = self.run_record()
        run = self.repository.transition(
            run.id,
            run.version,
            "waiting_signal",
            {"contract": contract(), "opening": OpeningRange(D(1510), D(1490))},
        )
        request = OrderRequest(
            "mysqlgttfixture", "option", "D", "BUY", "LIMIT", 500, D(100)
        )
        gtt = GttRepository()
        record = gtt.reserve(run, request, D(90), D(118))
        self.assertEqual(record.pending["kind"], "create")
        current = self.repository.get_run(run.id)
        self.assertEqual(current.execution_backend, "gtt")
        self.assertIsNone(gtt.reserve(current, request, D(90), D(118)))
        gtt.acknowledge(run.id, "GTT-" + secrets.token_hex(10))
        record = gtt.get(run.id)
        self.assertIsNone(record.pending)
        leg = GttLeg("ENTRY", "COMPLETED", D(100), "BUY", "fixture-order")
        snapshot = GttSnapshot(
            record.broker_id,
            (
                leg,
                GttLeg("TARGET", "SCHEDULED", D(118), "SELL"),
                GttLeg("STOPLOSS", "SCHEDULED", D(90), "SELL"),
            ),
        )
        gtt.observe(run.id, snapshot)
        self.assertEqual(gtt.get(run.id).snapshot, snapshot)
        intent = gtt.attach_child(run.id, leg, request)
        self.assertEqual(gtt.attach_child(run.id, leg, request).id, intent.id)
        order = BrokerOrder(
            "fixture-order",
            request.tag,
            "complete",
            500,
            500,
            (Fill("fixture-trade", "fixture-order", 500, D(100)),),
        )
        self.repository.apply_order(intent.id, order)
        self.repository.apply_order(intent.id, order)
        current = self.repository.get_run(run.id)
        self.assertEqual(
            (current.filled_quantity, current.remaining_quantity), (500, 500)
        )
        mutation = gtt.mutation(run.id, "modify", {"stop": "91", "target": "119"})
        self.assertEqual(gtt.get(run.id).pending["id"], mutation)
        gtt.levels(run.id, D(91), D(119))
        gtt.mutation_result(mutation, "confirmed")
        self.assertEqual(gtt.get(run.id).stop, D(91))

    def test_repaired_candle_gap_updates_decision_and_keeps_revision_event(self):
        from dataclasses import replace

        run = self.run_record()
        candles = [candle(), replace(candle(30, "1511"), start=at(9, 45))]
        opening = build_range(candles, at(10, 0))
        invalid = evaluate_breakout(candles, opening, at(10, 0), None)
        self.repository.record_decision(run.id, invalid, at(10, 0))
        candles.insert(1, candle(30, "1500"))
        valid = evaluate_breakout(candles, opening, at(10, 0, 2), None)
        self.repository.record_decision(run.id, valid, at(10, 0, 2))
        rows, _, events = self.repository.history_details(run.id)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["outcome"], "signal")
        self.assertTrue(any(e["kind"] == "decision_revised" for e in events))
        expired = evaluate_breakout(candles, opening, at(10, 2), None)
        self.repository.record_decision(run.id, expired, at(10, 2))
        rows, _, _ = self.repository.history_details(run.id)
        self.assertEqual(rows[0]["outcome"], "signal")
