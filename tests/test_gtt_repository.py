import io
import unittest
from datetime import date
from decimal import Decimal as D

from alembic import command
from alembic.config import Config

from app.repositories.strategy import run_from_row
from app.strategies.rules import premium_levels


class GttDomainTests(unittest.TestCase):
    def test_retry_migration_preserves_old_journals_and_adds_attempt_scope(self):
        out = io.StringIO()
        command.upgrade(Config("alembic.ini", output_buffer=out), "head", sql=True)
        sql = out.getvalue()
        self.assertIn("CREATE TABLE strategy_gtt_attempt_history", sql)
        self.assertIn("ADD COLUMN attempt", sql)
        self.assertIn("ADD COLUMN retry_requested", sql)
        self.assertIn("PRIMARY KEY (run_id, attempt, strategy)", sql)
        self.assertNotIn("DROP TABLE strategy_gtt_mutations", sql)

    def test_stop_is_five_and_target_is_eight_points_from_entry(self):
        self.assertEqual(
            premium_levels(D("101.50"), D("0.05")), (D("96.50"), D("109.50"))
        )

    def test_order_request_accepts_intraday_and_delivery_products(self):
        from app.strategies.models import OrderRequest

        for product in ("I", "D"):
            OrderRequest(
                "gttproduct", "option", product, "BUY", "LIMIT", 500, D(100)
            )
        with self.assertRaises(ValueError):
            OrderRequest(
                "gttproduct", "option", "X", "BUY", "LIMIT", 500, D(100)
            )

    def test_historical_backend_defaults_to_orders(self):
        row = {
            "id": 1,
            "client_id": "a",
            "user_id": "u",
            "strategy": "reliance_orb",
            "mode": "live",
            "exchange_date": date(2026, 10, 19),
        }
        self.assertEqual(run_from_row(row).execution_backend, "orders")
        row["execution_backend"] = "gtt"
        self.assertEqual(run_from_row(row).execution_backend, "gtt")

    def test_migration_has_durable_parent_mutation_and_leg_mapping(self):
        out = io.StringIO()
        command.upgrade(Config("alembic.ini", output_buffer=out), "head", sql=True)
        sql = out.getvalue()
        for fragment in [
            "ADD COLUMN execution_backend",
            "CREATE TABLE strategy_gtts",
            "CREATE TABLE strategy_gtt_mutations",
            "CREATE TABLE strategy_gtt_legs",
        ]:
            self.assertIn(fragment, sql)

    def test_record_round_trip_preserves_parent_legs_and_uncertain_mutation(self):
        from dataclasses import asdict

        from app.repositories.gtt import record_from_row
        from app.repositories.strategy import dump
        from app.strategies.gtt_models import GttLeg, GttSnapshot
        from app.strategies.models import OrderRequest
        from tests.test_strategy_rules import at

        req = OrderRequest("gttroundtrip", "option", "D", "BUY", "LIMIT", 500, D(100))
        snapshot = GttSnapshot(
            "GTT-1",
            (
                GttLeg("ENTRY", "COMPLETED", D(100), "BUY", "o1"),
                GttLeg("TARGET", "SCHEDULED", D(118), "SELL"),
                GttLeg("STOPLOSS", "SCHEDULED", D(90), "SELL"),
            ),
        )
        row = {
            "run_id": 1,
            "request": dump(asdict(req)),
            "stop": D(90),
            "target": D(118),
            "created_at": at(),
            "broker_id": "GTT-1",
            "snapshot": dump(asdict(snapshot)),
        }
        record = record_from_row(
            row,
            {
                "id": 4,
                "kind": "modify",
                "fields": dump({"stop": "89.5", "target": "117.5"}),
            },
        )
        self.assertEqual(record.request, req)
        self.assertEqual(record.snapshot, snapshot)
        self.assertEqual(record.pending["fields"]["target"], "117.5")

    def test_unsupported_leg_and_duplicate_child_are_rejected(self):
        from app.strategies.gtt_models import GttLeg, GttSnapshot

        with self.assertRaises(ValueError):
            GttLeg("TARGET", "UNKNOWN", D(118), "SELL")
        with self.assertRaises(ValueError):
            GttLeg("TARGET", "COMPLETED", D(118), "SELL")
        with self.assertRaises(ValueError):
            GttSnapshot(
                "GTT-1",
                (
                    GttLeg("ENTRY", "COMPLETED", D(100), "BUY", "o1"),
                    GttLeg("TARGET", "COMPLETED", D(118), "SELL", "o1"),
                    GttLeg("STOPLOSS", "SCHEDULED", D(90), "SELL"),
                ),
            )

    def test_gtt_downgrade_keeps_historical_strategy_and_tokens(self):
        out = io.StringIO()
        command.downgrade(
            Config("alembic.ini", output_buffer=out), "0005:0004", sql=True
        )
        sql = out.getvalue()
        self.assertIn("DROP TABLE strategy_gtt_legs", sql)
        self.assertIn("DROP COLUMN execution_backend", sql)
        self.assertNotIn("DROP TABLE strategy_orders", sql)
        self.assertNotIn("DROP TABLE upstox_tokens", sql)

    def test_reservation_persists_parent_and_creation_before_consuming_allowance(self):
        from dataclasses import asdict
        from unittest.mock import MagicMock, patch

        from app.repositories.gtt import GttRepository
        from app.repositories.strategy import dump
        from app.strategies.models import OrderRequest, RunKey, StrategyRun
        from tests.test_strategy_rules import at, contract

        run = StrategyRun(
            1,
            RunKey("a", "u", "reliance_orb", "live", at().date()),
            state="waiting_signal",
            contract=contract(),
        )
        conn = MagicMock()
        cursor = conn.cursor.return_value.__enter__.return_value
        cursor.rowcount = 1
        cursor.lastrowid = 1
        cursor.fetchone.return_value = {
            "id": 1,
            "client_id": "a",
            "user_id": "u",
            "strategy": "reliance_orb",
            "mode": "live",
            "exchange_date": at().date(),
            "version": 0,
            "state": "waiting_signal",
            "contract": dump(asdict(contract())),
        }
        req = OrderRequest("reservegtt", "option", "D", "BUY", "LIMIT", 500, D(100))
        with patch("app.repositories.strategy.get_connection", return_value=conn):
            record = GttRepository().reserve(run, req, D(90), D(118))
        statements = [call.args[0] for call in cursor.execute.call_args_list]
        self.assertIn("FOR UPDATE", statements[0])
        self.assertTrue(statements[1].startswith("INSERT INTO strategy_gtts"))
        self.assertTrue(statements[2].startswith("INSERT INTO strategy_gtt_mutations"))
        self.assertTrue(statements[3].startswith("UPDATE strategy_runs"))
        self.assertEqual(record.pending["kind"], "create")
        conn.commit.assert_called_once()
        conn.close.assert_called_once()

    def test_cancel_supersedes_pending_modify_in_one_transaction(self):
        from unittest.mock import MagicMock, patch

        from app.repositories.gtt import GttRepository
        from app.strategies.models import RunKey, StrategyRun
        from tests.test_strategy_rules import at

        conn = MagicMock()
        cursor = conn.cursor.return_value.__enter__.return_value
        cursor.fetchone.return_value = {"id": 7, "kind": "modify"}
        cursor.lastrowid = 8
        run = StrategyRun(1, RunKey("a", "u", "reliance_orb", "live", at().date()))
        with (
            patch("app.repositories.strategy.get_connection", return_value=conn),
            patch("app.repositories.gtt._read", return_value=run),
        ):
            self.assertEqual(GttRepository().mutation(1, "cancel", {}), 8)
        statements = [call.args[0] for call in cursor.execute.call_args_list]
        self.assertIn("status='superseded'", statements[1])
        self.assertTrue(statements[2].startswith("INSERT INTO strategy_gtt_mutations"))
        conn.commit.assert_called_once()

    def test_cancellation_cannot_supersede_unknown_creation_or_cancel(self):
        from unittest.mock import MagicMock, patch

        from app.repositories.gtt import GttRepository

        for kind in ["create", "cancel", "cancel_child"]:
            conn = MagicMock()
            cursor = conn.cursor.return_value.__enter__.return_value
            cursor.fetchone.return_value = {"id": 7, "kind": kind}
            with (
                patch("app.repositories.strategy.get_connection", return_value=conn),
                patch("app.repositories.gtt._read"),
                self.assertRaises(ValueError),
            ):
                GttRepository().mutation(1, "cancel", {})
            self.assertFalse(
                any(
                    call.args[0].startswith("INSERT")
                    for call in cursor.execute.call_args_list
                )
            )
            conn.rollback.assert_called_once()
