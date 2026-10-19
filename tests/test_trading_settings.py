import unittest
from unittest.mock import patch

from app.core.trading import load_trading_settings


class TradingSettingsTests(unittest.TestCase):
    def settings(self, **env):
        with (
            patch.dict("os.environ", env, clear=True),
            patch("app.core.trading.load_dotenv"),
        ):
            return load_trading_settings()

    def test_execution_backend(self):
        self.assertEqual(self.settings().execution_backend, "gtt")
        self.assertEqual(
            self.settings(ORB_EXECUTION_BACKEND="orders").execution_backend, "orders"
        )
        with self.assertRaises(ValueError):
            self.settings(ORB_EXECUTION_BACKEND="invalid")

    def test_default_is_paper_live_disabled(self):
        s = self.settings()
        self.assertEqual(s.mode, "paper")
        self.assertFalse(s.live_enabled)
        self.assertEqual(s.poll_seconds, 2)

    def test_live_requires_explicit_account_flag_and_limits(self):
        with self.assertRaises(ValueError):
            self.settings(ORB_MODE="live")
        with self.assertRaises(ValueError):
            self.settings(
                ORB_MODE="live", ORB_LIVE_ENABLED="true", ORB_BROKER_USER_ID="u"
            )
        s = self.settings(
            ORB_MODE="live",
            ORB_LIVE_ENABLED="true",
            ORB_BROKER_USER_ID="u",
            ORB_MAX_PREMIUM="100",
            ORB_MAX_NOTIONAL="100000",
            ORB_MAX_PLANNED_LOSS="10000",
            ORB_MAX_SPREAD="1",
            ORB_MAX_SLIPPAGE="1",
        )
        self.assertEqual(s.user_id, "u")

    def test_invalid_settings_fail_closed(self):
        for env in [
            {"ORB_MODE": "oops"},
            {"ORB_LIVE_ENABLED": "yes"},
            {"ORB_MAX_PREMIUM": "NaN"},
            {"ORB_POLL_SECONDS": "0"},
            {"ORB_MAX_SLIPPAGE": "Infinity"},
        ]:
            with self.subTest(env=env), self.assertRaises(ValueError):
                self.settings(**env)

    def test_explicit_uncapped_spend_preserves_required_risk_limits(self):
        values = {
            "ORB_MODE": "live",
            "ORB_LIVE_ENABLED": "true",
            "ORB_BROKER_USER_ID": "u",
            "ORB_MAX_PREMIUM": "none",
            "ORB_MAX_NOTIONAL": "none",
            "ORB_MAX_PLANNED_LOSS": "10000",
            "ORB_MAX_SPREAD": "1",
            "ORB_MAX_SLIPPAGE": "1",
        }
        settings = self.settings(**values)
        self.assertIsNone(settings.limits.max_premium)
        self.assertIsNone(settings.limits.max_notional)
        for key in ("ORB_MAX_PLANNED_LOSS", "ORB_MAX_SPREAD", "ORB_MAX_SLIPPAGE"):
            with self.subTest(key=key), self.assertRaises(ValueError):
                self.settings(**{**values, key: "none"})
        with self.assertRaises(ValueError):
            self.settings(**{**values, "ORB_MAX_PREMIUM": ""})
