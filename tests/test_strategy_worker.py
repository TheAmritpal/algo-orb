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
        credential_lock = patch("app.cli.run_strategy.WorkerLock", autospec=True)
        self.credential_lock = credential_lock.start()
        self.credential_lock.return_value.acquire.return_value = True
        self.addCleanup(credential_lock.stop)
        profile = patch(
            "app.cli.run_strategy.sdk_data", return_value=N(user_id="paper")
        )
        self.profile = profile.start()
        self.addCleanup(profile.stop)
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

    def test_new_token_is_adopted_without_rebuilding_execution_state(self):
        c = self.components()
        self.lookup.side_effect = [
            N(access_token="startup-token"),
            N(access_token="new"),
        ]
        stop = Event()
        seen = []

        def tick(_):
            seen.append(c.client.configuration.access_token)
            if len(seen) == 2:
                stop.set()

        c.engine.tick.side_effect = tick
        with (
            patch("app.cli.run_strategy.build_components", return_value=c) as build,
            patch.object(stop, "wait"),
        ):
            self.assertEqual(run_worker(TradingSettings(), stop), 0)
        self.assertEqual(seen, ["startup-token", "new"])
        build.assert_called_once()
        c.engine.recover.assert_called_once()

    def test_busy_renewal_skips_iteration_without_submitting(self):
        c = self.components()
        self.credential_lock.return_value.acquire.side_effect = [False, True]
        stop = Event()
        c.engine.tick.side_effect = lambda _: stop.set()
        with (
            patch("app.cli.run_strategy.build_components", return_value=c),
            patch.object(stop, "wait"),
        ):
            self.assertEqual(run_worker(TradingSettings(), stop), 0)
        c.engine.tick.assert_called_once()
        self.assertEqual(self.credential_lock.return_value.acquire.call_count, 2)

    def test_missing_or_wrong_account_token_never_reaches_tick(self):
        for token, profile in [
            (None, N(user_id="paper")),
            (N(access_token="new"), N(user_id="OTHER")),
        ]:
            with self.subTest(token=token):
                c = self.components()
                self.lookup.return_value = token
                self.profile.return_value = profile
                stop = Event()
                with (
                    patch("app.cli.run_strategy.build_components", return_value=c),
                    patch.object(
                        stop, "wait", side_effect=lambda _, event=stop: event.set()
                    ),
                ):
                    self.assertEqual(run_worker(TradingSettings(), stop), 0)
                c.engine.tick.assert_not_called()
                self.assertEqual(c.client.configuration.access_token, "startup-token")

    def test_startup_waits_for_renewal_without_process_restart(self):
        c = self.components()
        stop = Event()
        c.engine.tick.side_effect = lambda _: stop.set()
        with (
            patch(
                "app.cli.run_strategy.build_components",
                side_effect=[RuntimeError("SECRET"), c],
            ) as build,
            patch.object(stop, "wait"),
        ):
            self.assertEqual(run_worker(TradingSettings(), stop), 0)
        self.assertEqual(build.call_count, 2)
        c.engine.tick.assert_called_once()

    def test_failed_lease_cleanup_reacquires_and_reconciles_without_restart(self):
        c = self.components()
        stop = Event()
        self.credential_lock.return_value.close.side_effect = [
            RuntimeError("SECRET"),
            None,
        ]
        calls = []

        def tick(_):
            calls.append(1)
            if len(calls) == 2:
                stop.set()

        c.engine.tick.side_effect = tick
        with (
            patch("app.cli.run_strategy.build_components", return_value=c),
            patch.object(stop, "wait"),
        ):
            self.assertEqual(run_worker(TradingSettings(), stop), 0)
        self.assertEqual(c.engine.recover.call_count, 2)
        self.assertEqual(len(calls), 2)

    def test_startup_ownership_failure_disposes_candidate_and_retries(self):
        failed = self.components()
        failed.lock.acquire.side_effect = RuntimeError("SECRET")
        ready = self.components()
        stop = Event()
        ready.engine.tick.side_effect = lambda _: stop.set()
        with (
            patch("app.cli.run_strategy.build_components", side_effect=[failed, ready]),
            patch.object(
                stop,
                "wait",
                side_effect=[None, AssertionError("startup did not recover")],
            ),
        ):
            self.assertEqual(run_worker(TradingSettings(), stop), 0)
        failed.lock.close.assert_called_once()
        failed.broker.close.assert_called_once()
        failed.engine.recover.assert_not_called()
        ready.engine.tick.assert_called_once()

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
