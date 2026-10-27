"""CLI safety boundaries for the one-time manual-exit repair."""

import unittest
from dataclasses import replace
from datetime import date
from decimal import Decimal as D
from types import SimpleNamespace as N
from unittest.mock import MagicMock, patch

from app.strategies.models import OrderIntent, OrderRequest, RunKey, StrategyRun
from tests.test_strategy_rules import at, contract


class RepairCliTests(unittest.TestCase):
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
        self.entry_intent = OrderIntent(
            7,
            160103,
            "entry",
            OrderRequest(
                "gtt00fbb686bdb647ef9",
                "option",
                "D",
                "BUY",
                "LIMIT",
                1000,
                D("45.9"),
            ),
            at(),
            broker_id="261026000111597",
        )
        self.repo = MagicMock()
        self.repo.get_run.return_value = self.run
        self.repo.list_intents.return_value = [self.entry_intent]
        self.repo.repair_manual_exit.return_value = {
            "applied": False,
            "state": "closed",
        }
        self.lock = MagicMock()
        self.lock.acquire.return_value = True
        self.client = MagicMock()
        self.broker = MagicMock()
        self.broker.positions.return_value = []
        self.stack = []
        import app.cli.repair_manual_exit as cli

        self.cli = cli
        for name, value in {
            "StrategyRepository": self.repo,
            "get_upstox_config": {"client_id": "app"},
            "get_token": N(access_token="private-token"),
            "Broker": self.broker,
            "WorkerLock": self.lock,
            "sdk_data": N(user_id="user"),
        }.items():
            patcher = patch(
                "app.cli.repair_manual_exit." + name,
                return_value=value,
            )
            patcher.start()
            self.stack.append(patcher)
        for name in ("ApiClient", "Configuration", "UserApi"):
            patcher = patch(
                "app.cli.repair_manual_exit.sdk." + name,
                return_value=self.client,
            )
            patcher.start()
            self.stack.append(patcher)

    def tearDown(self):
        for patcher in reversed(self.stack):
            patcher.stop()

    def reconcile(self, apply=False):
        return self.cli.reconcile_manual_exit({"run_id": 160103}, apply=apply)

    def test_dry_run_takes_no_worker_lock(self):
        self.reconcile()
        self.lock.acquire.assert_not_called()
        self.assertFalse(
            self.repo.repair_manual_exit.call_args.kwargs["apply"]
        )

    def test_apply_refuses_when_worker_is_running(self):
        self.lock.acquire.return_value = False
        with self.assertRaises(ValueError):
            self.reconcile(apply=True)
        self.repo.repair_manual_exit.assert_not_called()

    def test_nonflat_position_refuses(self):
        self.broker.positions.return_value = [
            N(instrument="option", product="D", quantity=1000)
        ]
        with self.assertRaises(ValueError):
            self.reconcile(apply=True)
        self.repo.repair_manual_exit.assert_not_called()

    def test_resources_close_on_error(self):
        self.repo.repair_manual_exit.side_effect = RuntimeError("secret")
        with self.assertRaises(RuntimeError):
            self.reconcile()
        self.client.rest_client.pool_manager.clear.assert_called_once()
        self.client.pool.close.assert_called_once()
        self.client.pool.join.assert_called_once()
