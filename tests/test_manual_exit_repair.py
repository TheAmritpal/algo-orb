"""Atomic manual-exit repair for the 2026-10-26 incident."""

import unittest
from dataclasses import replace
from datetime import date
from decimal import Decimal as D
from unittest.mock import MagicMock, call, patch

from app.repositories.strategy import StrategyRepository
from app.strategies.models import (
    BrokerOrder,
    Fill,
    OrderIntent,
    OrderRequest,
    RunKey,
    StrategyRun,
)
from tests.test_strategy_rules import at, contract


class ManualExitRepairTests(unittest.TestCase):
    def setUp(self):
        self.run = StrategyRun(
            160103,
            RunKey("app", "user", "reliance_orb", "live", date(2026, 10, 26)),
            version=5,
            state="reconciliation_required",
            contract=replace(
                contract("1190", "CE"), expiry=date(2026, 11, 23)
            ),
            requested_quantity=1000,
            entry_reserved=True,
            execution_backend="gtt",
        )
        self.entry_request = OrderRequest(
            "gtt00fbb686bdb647ef9", "option", "D", "BUY", "LIMIT", 1000, D("45.9")
        )
        self.entry_intent = OrderIntent(
            7,
            160103,
            "entry",
            self.entry_request,
            at(),
            broker_id="261026000111597",
        )
        self.entry_order = BrokerOrder(
            "261026000111597",
            self.entry_request.tag,
            "complete",
            1000,
            1000,
            (
                Fill("370751", "261026000111597", 500, D("45.75")),
                Fill("370752", "261026000111597", 500, D("45.80")),
            ),
        )
        self.exit_request = OrderRequest(
            "hist1234567890abcdef",
            "option",
            "D",
            "SELL",
            "LIMIT",
            1000,
            D("45.95"),
        )
        self.exit_order = BrokerOrder(
            "histfill1234567890abcdef",
            self.exit_request.tag,
            "complete",
            1000,
            1000,
            (
                Fill("753152", "histfill1234567890abcdef", 500, D("45.95")),
                Fill("753153", "histfill1234567890abcdef", 500, D("45.95")),
            ),
        )
        self.exit_intent = OrderIntent(
            8,
            160103,
            "exit",
            self.exit_request,
            at(),
            broker_id=self.exit_order.broker_id,
            status="complete",
            fills=list(self.exit_order.fills),
        )
        self.conn = MagicMock()
        self.cursor = self.conn.cursor.return_value.__enter__.return_value
        self.cursor.fetchall.return_value = []
        self.cursor.fetchone.return_value = None
        self.cursor.lastrowid = 8
        self.stack = []

    def tearDown(self):
        for patcher in reversed(self.stack):
            patcher.stop()

    def test_dry_run_projects_closed_without_writes(self):
        with (
            patch(
                "app.repositories.strategy.StrategyRepository.list_intents",
                return_value=[self.entry_intent],
            ),
        ):
            result = StrategyRepository().repair_manual_exit(
                self.run,
                self.entry_order,
                self.exit_request,
                self.exit_order,
                apply=False,
            )
        self.assertEqual(
            (
                result["state"],
                result["filled_quantity"],
                result["remaining_quantity"],
                result["entry_average"],
                result["applied"],
            ),
            ("closed", 1000, 0, D("45.775"), False),
        )
        statements = [
            c.args[0].lstrip().upper() for c in self.cursor.execute.call_args_list
        ]
        self.assertFalse(
            any(s.startswith(("INSERT", "UPDATE", "DELETE")) for s in statements)
        )

    def test_apply_writes_entry_exit_and_closed_state_atomically(self):
        entry_run = replace(self.run, version=6, filled_quantity=1000, remaining_quantity=1000)
        flat_run = replace(entry_run, version=8, remaining_quantity=0)
        with (
            patch(
                "app.repositories.strategy.get_connection",
                return_value=self.conn,
            ),
            patch(
                "app.repositories.strategy.StrategyRepository.list_intents",
                return_value=[self.entry_intent],
            ),
            patch("app.repositories.strategy._read", return_value=self.run),
            patch(
                "app.repositories.strategy._intents",
                return_value=[self.entry_intent],
            ),
            patch.object(
                StrategyRepository, "_insert", return_value=self.exit_intent
            ),
            patch.object(
                StrategyRepository,
                "_apply_order",
                side_effect=[entry_run, flat_run],
            ) as apply_order,
            patch("app.repositories.strategy._save") as save,
        ):
            result = StrategyRepository().repair_manual_exit(
                self.run,
                self.entry_order,
                self.exit_request,
                self.exit_order,
                apply=True,
            )
        self.assertTrue(result["applied"])
        self.assertEqual(result["entry_average"], D("45.775"))
        self.assertEqual(
            apply_order.call_args_list,
            [call(self.cursor, 7, self.entry_order), call(self.cursor, 8, self.exit_order)],
        )
        self.assertEqual(save.call_args.args[1].state, "closed")
        self.conn.commit.assert_called_once()
        self.conn.rollback.assert_not_called()
        inserts = [
            c.args[1]
            for c in self.cursor.execute.call_args_list
            if c.args[0].startswith("INSERT INTO strategy_events")
        ]
        self.assertEqual(len(inserts), 1)
        self.assertEqual(inserts[0][2], "historical_reconciled")
        self.assertIn("370751", str(inserts[0][3]))

    def test_stale_version_refuses_apply_without_commit(self):
        with (
            patch(
                "app.repositories.strategy.get_connection",
                return_value=self.conn,
            ),
            patch(
                "app.repositories.strategy.StrategyRepository.list_intents",
                return_value=[self.entry_intent],
            ),
            patch(
                "app.repositories.strategy._read",
                return_value=replace(self.run, version=6),
            ),
            patch("app.repositories.strategy._intents", return_value=[]),
            patch.object(StrategyRepository, "_insert"),
            patch.object(StrategyRepository, "_apply_order"),
            patch("app.repositories.strategy._save"),
            self.assertRaises(ValueError),
        ):
            StrategyRepository().repair_manual_exit(
                self.run,
                self.entry_order,
                self.exit_request,
                self.exit_order,
                apply=True,
            )
        self.conn.commit.assert_not_called()

    def test_conflicting_existing_fill_refuses_even_dry_run(self):
        self.entry_intent.fills = [
            Fill("370751", "261026000111597", 500, D("1"))
        ]
        with (
            patch(
                "app.repositories.strategy.StrategyRepository.list_intents",
                return_value=[self.entry_intent],
            ),
            self.assertRaises(ValueError),
        ):
            StrategyRepository().repair_manual_exit(
                self.run,
                self.entry_order,
                self.exit_request,
                self.exit_order,
                apply=False,
            )

    def test_identical_closed_replay_is_idempotent(self):
        closed = replace(
            self.run,
            version=9,
            state="closed",
            filled_quantity=1000,
            remaining_quantity=0,
            entry_average=D("45.775"),
        )
        self.entry_intent.status = "complete"
        self.entry_intent.fills = list(self.entry_order.fills)
        with (
            patch(
                "app.repositories.strategy.StrategyRepository.list_intents",
                return_value=[self.entry_intent, self.exit_intent],
            ),
            patch.object(StrategyRepository, "_apply_order") as apply_order,
            patch("app.repositories.strategy._save") as save,
        ):
            result = StrategyRepository().repair_manual_exit(
                closed,
                self.entry_order,
                self.exit_request,
                self.exit_order,
                apply=True,
            )
        self.assertFalse(result["applied"])
        self.assertEqual(result["state"], "closed")
        apply_order.assert_not_called()
        save.assert_not_called()
