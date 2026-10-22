import unittest
from contextlib import ExitStack
from datetime import timedelta
from types import SimpleNamespace as N
from unittest.mock import MagicMock, patch

from upstox_client.rest import ApiException

from app.core.trading import TradingSettings
from app.services import broker_readiness as readiness
from tests.test_strategy_rules import at, D
from app.strategies.models import Quote
from app.strategies.rules import UNDERLYING


class ReadinessTests(unittest.TestCase):
    def setUp(self):
        readiness._checks.clear()
        self.settings = TradingSettings(client_id="app", user_id="pinned")
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.token = self.stack.enter_context(patch.object(readiness, "get_token", return_value=N(access_token="secret", user_id="pinned")))
        self.stack.enter_context(patch.object(readiness.sdk, "Configuration"))
        self.stack.enter_context(patch.object(readiness.sdk, "ApiClient", return_value=MagicMock()))
        self.user = self.stack.enter_context(patch.object(readiness.sdk, "UserApi"))
        self.user.return_value.get_profile.return_value = N(status="success", data=N(user_id="pinned"))
        self.stack.enter_context(patch.object(readiness, "sdk_data", return_value=[]))
        self.active = self.stack.enter_context(patch.object(readiness, "normal_session", return_value=True))
        self.market = self.stack.enter_context(patch.object(readiness, "MarketData"))
        self.market.return_value.quote.return_value = Quote(UNDERLYING, at(), D(100), D(101), D(100))

    def check(self, now=None):
        return readiness.get_broker_readiness(self.settings, now=now or at())

    def test_current_quote_and_pinned_token_are_verified(self):
        result = self.check()
        self.assertEqual((result.token_state, result.quote_state), ("valid", "fresh"))
        self.assertEqual(result.last_success_at, at())
        self.token.assert_called_once_with("app", "pinned")

    def test_expired_token_does_not_report_quote_ready_or_expose_secrets(self):
        self.user.return_value.get_profile.side_effect = ApiException(status=401, reason="secret")
        result = self.check()
        self.assertEqual((result.token_state, result.quote_state), ("invalid", "unknown"))
        self.assertIsNone(result.last_success_at)
        self.assertNotIn("secret", repr(result))

    def test_profile_timeout_is_unavailable_and_retains_previous_success(self):
        self.check()
        self.user.return_value.get_profile.side_effect = TimeoutError("secret")
        result = self.check(at() + timedelta(seconds=11))
        self.assertEqual((result.token_state, result.quote_state), ("unavailable", "unknown"))
        self.assertEqual(result.last_success_at, at())
        self.assertNotIn("secret", repr(result))

    def test_stale_and_future_quotes_fail_during_session(self):
        for seconds in (-6, 1):
            readiness._checks.clear()
            self.market.return_value.quote.return_value = Quote(UNDERLYING, at()+timedelta(seconds=seconds), D(100), D(101), D(100))
            result = self.check()
            self.assertEqual(result.quote_state, "stale")
            self.assertIsNone(result.last_success_at)

    def test_quote_outage_preserves_last_success_after_cache_expires(self):
        self.check()
        self.market.return_value.quote.side_effect = RuntimeError("secret")
        result = self.check(at()+timedelta(seconds=11))
        self.assertEqual((result.token_state, result.quote_state), ("valid", "unavailable"))
        self.assertEqual(result.last_success_at, at())
        self.assertNotIn("secret", repr(result))

    def test_closed_market_does_not_label_old_quote_an_outage(self):
        self.active.return_value = False
        self.market.return_value.quote.return_value = Quote(UNDERLYING, at()-timedelta(hours=1), D(100), D(101), D(100))
        result = self.check()
        self.assertEqual(result.quote_state, "market_closed")
        self.assertIsNone(result.last_success_at)
        self.market.return_value.quote.assert_not_called()

    def test_missing_or_wrong_account_credentials_fail_closed(self):
        self.token.return_value = None
        self.assertEqual(self.check().token_state, "missing")
        readiness._checks.clear()
        self.token.return_value = N(access_token="secret")
        self.user.return_value.get_profile.return_value.data.user_id = "other"
        self.assertEqual(self.check().token_state, "invalid")

    def test_cache_bounds_requests_and_rechecks_after_ten_seconds(self):
        self.check()
        cached = self.check(at()+timedelta(seconds=9))
        self.assertEqual(cached.quote_state, "stale")
        self.assertEqual(self.user.return_value.get_profile.call_count, 1)
        self.check(at()+timedelta(seconds=11))
        self.assertEqual(self.user.return_value.get_profile.call_count, 2)
