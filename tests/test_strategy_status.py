import asyncio
import unittest
from datetime import timedelta
from unittest.mock import patch

from app.core.trading import TradingSettings
from app.main import app
from app.repositories.sessions import Session
from app.services.auth import require_session
from app.services.broker_readiness import BrokerReadiness
from app.services.strategy_status import get_strategy_status
from app.services.upstox import ConnectionStatus
from app.strategies.models import RunKey, StrategyRun
from tests.http import asgi_request
from tests.test_strategy_rules import D, at, contract


class StatusTests(unittest.TestCase):
    def setUp(self):
        self.readiness = patch(
            "app.services.strategy_status.get_broker_readiness",
            create=True,
            return_value=BrokerReadiness("invalid", "unknown", checked_at=at()),
        )
        self.readiness.start()
        self.addCleanup(self.readiness.stop)
        self.settings = TradingSettings(user_id="u", client_id="app")
        self.run = StrategyRun(
            1,
            RunKey("app", "u", "reliance_orb", "paper", at().date()),
            state="open",
            contract=contract(),
            remaining_quantity=500,
            entry_average=D("100"),
            stop=D("90"),
            target=D("120"),
            heartbeat=at(),
            entry_reserved=True,
        )

    def status(self, run=None, now=None, error=None):
        with (
            patch(
                "app.services.strategy_status.load_trading_settings",
                return_value=self.settings,
            ),
            patch("app.services.strategy_status.StrategyRepository") as repo,
        ):
            repo.return_value.list_unresolved.return_value = [run] if run else []
            repo.return_value.latest.return_value = run
            if error:
                repo.return_value.list_unresolved.side_effect = error
            return get_strategy_status(now=now or at())

    def test_empty_running_and_stale_states(self):
        self.assertEqual(self.status().state, "not_started")
        result = self.status(self.run)
        self.assertEqual(
            (result.mode, result.worker_alive, result.run.remaining_quantity),
            ("paper", True, 500),
        )
        result = self.status(self.run, at() + timedelta(seconds=31))
        self.assertFalse(result.worker_alive)
        self.assertIn("exposure", result.message)

    def test_gtt_active_and_closing_messages_are_specific(self):
        for state, fragment in (("gtt_active", "15:14"), ("closing", "exit")):
            with self.subTest(state=state):
                self.run.state = state
                result = self.status(self.run)
                self.assertIn(fragment, result.message.lower())

    def test_current_heartbeat_cannot_hide_invalid_broker_token(self):
        result = self.status(self.run)
        self.assertTrue(result.worker_alive)
        self.assertEqual(result.broker.token_state, "invalid")
        self.assertIn("Broker unavailable", result.broker.label)

    def test_errors_hide_secrets_and_prior_day_exposure_is_visible(self):
        result = self.status(error=RuntimeError("secret-token"))
        self.assertEqual(result.state, "unavailable")
        self.assertNotIn("secret-token", repr(result))
        result = self.status(self.run, at() + timedelta(days=1))
        self.assertEqual(result.run.remaining_quantity, 500)
        self.assertFalse(result.worker_alive)

    def test_dashboard_renders_NRML_risk_deadline_and_paper_label(self):
        app.dependency_overrides[require_session] = lambda: Session(
            1, "admin", "csrf", at()
        )
        self.addCleanup(app.dependency_overrides.clear)
        with (
            patch(
                "app.routers.pages.get_strategy_status",
                return_value=self.status(self.run),
            ),
            patch(
                "app.routers.pages.connection_status",
                return_value=ConnectionStatus("connected", "u"),
            ),
        ):
            status, _, body = asyncio.run(asgi_request("/"))
        self.assertEqual(status, 200)
        for value in (
            b"RELIANCE",
            b"NRML",
            b"Paper",
            b"15:25",
            b"15:00",
            b"500",
            b"90",
            b"120",
            b"Broker unavailable",
            b"Token validity",
            b"Quote freshness",
        ):
            self.assertIn(value, body)

    def test_gtt_backend_visible_in_status(self):
        self.run.execution_backend = "gtt"
        result = self.status(self.run)
        self.assertIn("GTT", result.message)

    def test_pending_paper_mode_keeps_active_live_exposure_visible(self):
        from dataclasses import replace

        self.run.key = replace(self.run.key, mode="live")
        with (
            patch(
                "app.services.strategy_status.load_trading_settings",
                return_value=self.settings,
            ),
            patch("app.services.strategy_status.StrategyRepository") as repo,
        ):
            repo.return_value.list_unresolved.side_effect = lambda client, user, mode: (
                [self.run] if mode == "live" else []
            )
            repo.return_value.latest.return_value = None
            result = get_strategy_status(now=at())
        self.assertEqual(result.mode, "live")
        self.assertTrue(result.worker_alive)
        self.assertEqual(result.run.remaining_quantity, 500)
