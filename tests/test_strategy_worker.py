import unittest
from threading import Event
from types import SimpleNamespace as N
from unittest.mock import MagicMock, patch

from app.cli.run_strategy import build_components, run_worker
from app.core.trading import TradingSettings
from tests.test_strategy_rules import at


class WorkerTests(unittest.TestCase):
    def setUp(self):
        lookup = patch(
            "app.cli.run_strategy.get_token",
            return_value=N(access_token="startup-token"),
        )
        self.lookup = lookup.start()
        self.addCleanup(lookup.stop)

    def components(self, acquired=True):
        lock = MagicMock(spec=["acquire", "assert_owned", "close"])
        lock.acquire.return_value = acquired
        return N(
            lock=lock,
            engine=MagicMock(),
            broker=MagicMock(),
            market=MagicMock(),
            client=MagicMock(configuration=N(access_token="startup-token")),
        )

    def test_duplicate_worker_refuses_before_engine_start(self):
        c = self.components(False)
        with patch("app.cli.run_strategy.build_components", return_value=c):
            self.assertEqual(run_worker(TradingSettings(), Event()), 2)
        c.engine.tick.assert_not_called()
        c.lock.close.assert_called_once()

    def test_token_changed_during_startup_refuses_before_recovery(self):
        c = self.components()
        self.lookup.return_value = N(access_token="replacement-token")
        stop = Event()
        stop.set()
        with patch("app.cli.run_strategy.build_components", return_value=c):
            self.assertEqual(run_worker(TradingSettings(), stop), 2)
        c.engine.recover.assert_not_called()
        c.engine.tick.assert_not_called()

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

    def test_worker_wires_simulation_and_live_gtt_without_order_submission(self):
        from app.services.gtt_broker import GttBroker
        from app.services.paper_gtt import PaperGttBroker

        with (
            patch(
                "app.cli.run_strategy.get_token", return_value=N(access_token="fake")
            ),
            patch("app.cli.run_strategy.sdk_data", return_value=N(user_id="u")),
        ):
            for mode, expected in [("paper", PaperGttBroker), ("live", GttBroker)]:
                with self.subTest(mode=mode):
                    components = build_components(
                        TradingSettings(mode=mode, user_id="u", client_id="app")
                    )
                    try:
                        self.assertIsInstance(components.engine.gtt.broker, expected)
                        self.assertEqual(
                            components.engine.settings.execution_backend, "gtt"
                        )
                    finally:
                        from app.services.trading_broker import Broker

                        Broker(components.client).close()
