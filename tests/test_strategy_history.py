import asyncio
import unittest
from datetime import date
from unittest.mock import MagicMock, patch

from app.main import app
from app.repositories.sessions import Session
from app.services.auth import require_session
from tests.http import asgi_request
from tests.test_strategy_rules import at


class HistoryTests(unittest.TestCase):
    def test_history_requires_login(self):
        status, _, _ = asyncio.run(asgi_request("/strategy/history"))
        self.assertEqual(status, 303)

    def test_empty_history_is_read_only_and_distinguishes_no_recorded_signals(self):
        app.dependency_overrides[require_session] = lambda: Session(
            1, "admin", "csrf", at()
        )
        self.addCleanup(app.dependency_overrides.clear)
        from app.core.trading import TradingSettings

        with (
            patch(
                "app.core.trading.load_trading_settings",
                return_value=TradingSettings(user_id="u", client_id="app"),
            ),
            patch(
                "app.repositories.strategy.StrategyRepository.history_runs",
                create=True,
                return_value=[],
            ),
        ):
            status, _, body = asyncio.run(asgi_request("/strategy/history"))
        self.assertEqual(status, 200)
        self.assertIn(b"No recorded sessions", body)
        self.assertIn(b"Paper", body)

    def test_requested_run_cannot_escape_account_history(self):
        from app.services import strategy_history

        self.assertTrue(hasattr(strategy_history, "get_history"))
        from app.core.trading import TradingSettings
        from app.strategies.models import RunKey, StrategyRun

        run = StrategyRun(
            1, RunKey("app", "u", "reliance_orb", "paper", date(2026, 10, 19))
        )
        repository = MagicMock()
        repository.history_runs.return_value = [run]
        with (
            patch(
                "app.core.trading.load_trading_settings",
                return_value=TradingSettings(user_id="u", client_id="app"),
            ),
            patch(
                "app.repositories.strategy.StrategyRepository", return_value=repository
            ),
        ):
            result = strategy_history.get_history(mode="paper", run_id=999)
        self.assertIsNone(result.selected)
        self.assertEqual(result.decisions, [])

    def test_history_renders_signal_and_block_reason_without_html_injection(self):
        from app.core.trading import TradingSettings
        from app.strategies.models import RunKey, StrategyRun

        run = StrategyRun(
            1,
            RunKey("app", "u", "reliance_orb", "live", at().date()),
            state="skipped",
            message="Entry blocked: protection unverified",
        )
        app.dependency_overrides[require_session] = lambda: Session(
            1, "admin", "csrf", at()
        )
        self.addCleanup(app.dependency_overrides.clear)
        decisions = [
            {
                "candle_close": at(),
                "close_price": "1489",
                "opening_high": "1510",
                "opening_low": "1490",
                "direction": "PE",
                "outcome": "signal",
            }
        ]
        events = [
            {
                "created_at": at(),
                "kind": "entry_blocked",
                "fields": {
                    "reason": "protection_unverified",
                    "instrument": "<script>bad()</script>",
                },
            }
        ]
        with (
            patch(
                "app.core.trading.load_trading_settings",
                return_value=TradingSettings(mode="live", user_id="u", client_id="app"),
            ),
            patch(
                "app.repositories.strategy.StrategyRepository.history_runs",
                return_value=[run],
            ),
            patch(
                "app.repositories.strategy.StrategyRepository.history_details",
                return_value=(decisions, [], events),
            ),
        ):
            status, _, body = asyncio.run(
                asgi_request("/strategy/history", query="mode=live")
            )
        self.assertEqual(status, 200)
        for expected in (
            b"PE",
            b"1489",
            b"protection_unverified",
            b"Live",
            b"09:45:00",
        ):
            self.assertIn(expected, body)
        self.assertNotIn(b"<script>", body)
        self.assertIn(b"&lt;script&gt;", body)

    def test_pending_gtt_creation_is_visible_before_exchange_child_exists(self):
        from app.core.trading import TradingSettings
        from app.strategies.gtt_models import GttRecord
        from app.strategies.models import OrderRequest, RunKey, StrategyRun
        from tests.test_strategy_rules import D

        run = StrategyRun(
            1,
            RunKey("app", "u", "reliance_orb", "live", at().date()),
            state="reconciliation_required",
            execution_backend="gtt",
            entry_reserved=True,
        )
        record = GttRecord(
            1,
            OrderRequest("auditgtt", "option", "D", "BUY", "LIMIT", 500, D(100)),
            D(90),
            D(118),
            at(),
            pending={"id": 1, "kind": "create", "fields": {}},
        )
        app.dependency_overrides[require_session] = lambda: Session(
            1, "admin", "csrf", at()
        )
        self.addCleanup(app.dependency_overrides.clear)
        with (
            patch(
                "app.core.trading.load_trading_settings",
                return_value=TradingSettings(mode="live", user_id="u", client_id="app"),
            ),
            patch(
                "app.repositories.strategy.StrategyRepository.history_runs",
                return_value=[run],
            ),
            patch(
                "app.repositories.strategy.StrategyRepository.history_details",
                return_value=([], [], []),
            ),
            patch("app.repositories.gtt.GttRepository.get", return_value=record),
        ):
            status, _, body = asyncio.run(asgi_request("/strategy/history"))
        self.assertEqual(status, 200)
        self.assertIn(b"GTT parent", body)
        self.assertIn(b"create", body)
        self.assertIn(b"Unconfirmed", body)
