import unittest
from datetime import UTC, date, datetime
from threading import Event
from unittest.mock import Mock, patch


class TokenScheduleTests(unittest.TestCase):
    def test_ist_window_and_weekdays_are_independent_of_host_timezone(self):
        from app.cli.token_scheduler import renewal_due

        for now, expected in [
            (datetime(2026, 10, 20, 2, 59, tzinfo=UTC), False),  # 08:29 IST
            (datetime(2026, 10, 20, 3, 0, tzinfo=UTC), True),  # 08:30 IST
            (datetime(2026, 10, 20, 4, 0, tzinfo=UTC), True),  # late startup
            (datetime(2026, 10, 20, 9, 59, tzinfo=UTC), True),
            (datetime(2026, 10, 20, 10, 0, tzinfo=UTC), False),  # 15:30 IST
            (datetime(2026, 10, 24, 3, 0, tzinfo=UTC), False),  # Saturday
            (datetime(2026, 10, 25, 3, 0, tzinfo=UTC), False),  # Sunday
        ]:
            with self.subTest(now=now):
                self.assertEqual(renewal_due(now, None), expected)
        self.assertFalse(
            renewal_due(datetime(2026, 10, 20, 3, 0, tzinfo=UTC), date(2026, 10, 20))
        )

    def test_renews_once_per_day_and_again_next_weekday(self):
        from app.cli.token_scheduler import run_scheduler

        stop = Event()
        clock = Mock(
            side_effect=[
                datetime(2026, 10, 20, 2, 59, tzinfo=UTC),
                datetime(2026, 10, 20, 3, 0, tzinfo=UTC),
                datetime(2026, 10, 20, 4, 0, tzinfo=UTC),
                datetime(2026, 10, 21, 3, 0, tzinfo=UTC),
            ]
        )
        renew = Mock(return_value="saved")
        iterations = []

        def wait(seconds):
            iterations.append(seconds)
            if len(iterations) == 4:
                stop.set()

        with patch.object(stop, "wait", side_effect=wait):
            self.assertEqual(run_scheduler(stop, clock=clock, renew=renew), 0)
        self.assertEqual(renew.call_count, 2)
        for call in renew.call_args_list:
            self.assertEqual(call.args, ())
            self.assertEqual(call.kwargs, {})  # Never --connect or trading start.

    def test_failure_is_not_retried_that_day_and_does_not_log_secrets(self):
        from app.cli.token_scheduler import run_scheduler

        stop = Event()
        renew = Mock(side_effect=RuntimeError("SECRET"))
        clock = Mock(return_value=datetime(2026, 10, 20, 3, 0, tzinfo=UTC))
        count = []

        def wait(seconds):
            count.append(seconds)
            if len(count) == 2:
                stop.set()

        with (
            patch.object(stop, "wait", side_effect=wait),
            self.assertLogs("app.cli.token_scheduler") as logs,
        ):
            self.assertEqual(run_scheduler(stop, clock=clock, renew=renew), 0)
        self.assertEqual(renew.call_count, 1)
        self.assertNotIn("SECRET", "\n".join(logs.output))
        self.assertIn("failed", "\n".join(logs.output).lower())

    def test_disconnected_outcome_is_logged_and_not_reconnected(self):
        from app.cli.token_scheduler import run_scheduler

        stop = Event()
        renew = Mock(return_value="disconnected")
        with (
            patch.object(stop, "wait", side_effect=lambda _: stop.set()),
            self.assertLogs("app.cli.token_scheduler") as logs,
        ):
            run_scheduler(
                stop,
                clock=lambda: datetime(2026, 10, 20, 3, 0, tzinfo=UTC),
                renew=renew,
            )
        renew.assert_called_once_with()
        self.assertIn("disconnected", "\n".join(logs.output))

    def test_stopped_scheduler_does_not_contact_broker(self):
        from app.cli.token_scheduler import run_scheduler

        stop = Event()
        stop.set()
        renew = Mock()
        self.assertEqual(run_scheduler(stop, renew=renew), 0)
        renew.assert_not_called()
