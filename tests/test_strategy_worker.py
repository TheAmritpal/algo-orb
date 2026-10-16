import unittest
from threading import Event
from types import SimpleNamespace as N
from unittest.mock import MagicMock, patch

from app.cli.run_strategy import build_components, run_worker
from app.core.trading import TradingSettings
from tests.test_strategy_rules import at


class WorkerTests(unittest.TestCase):
    def components(self, acquired=True):
        lock = MagicMock(spec=["acquire", "assert_owned", "close"])
        lock.acquire.return_value = acquired
        return N(lock=lock, engine=MagicMock(), broker=MagicMock(), market=MagicMock())

    def test_duplicate_worker_refuses_before_engine_start(self):
        c = self.components(False)
        with patch("app.cli.run_strategy.build_components", return_value=c):
            self.assertEqual(run_worker(TradingSettings(), Event()), 2)
        c.engine.tick.assert_not_called()
        c.lock.close.assert_called_once()

    def test_backoff_bounded_and_shutdown_disables_only_entries(self):
        c = self.components()
        c.engine.tick.side_effect = RuntimeError("do-not-log")
        stop = Event()
        waits = []

        def wait(delay):
            waits.append(delay)
            if len(waits) == 6:
                stop.set()

        with (
            patch("app.cli.run_strategy.build_components", return_value=c),
            patch.object(stop, "wait", side_effect=wait),
        ):
            self.assertEqual(run_worker(TradingSettings(), stop, clock=lambda: at()), 0)
        self.assertEqual(waits, [4, 8, 16, 30, 30, 30])
        c.engine.stop_entries.assert_called_once()
        c.broker.close.assert_called_once()

    def test_original_account_token_required_not_latest(self):
        with (
            patch("app.cli.run_strategy.get_token", return_value=None) as exact,
            self.assertRaisesRegex(ValueError, "selected broker account"),
        ):
            build_components(TradingSettings(user_id="pinned", client_id="app"))
        exact.assert_called_once_with("app", "pinned")

    def test_ownership_loss_stops_worker_without_tick(self):
        c = self.components()
        c.lock.assert_owned.side_effect = RuntimeError("lost")
        with patch("app.cli.run_strategy.build_components", return_value=c):
            self.assertEqual(run_worker(TradingSettings(), Event()), 2)
        c.engine.tick.assert_not_called()
