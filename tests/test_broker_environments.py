import io
import os
import unittest
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from alembic import command
from alembic.config import Config


class EnvironmentTests(unittest.TestCase):
    def test_live_prefixed_credentials_are_used_together(self):
        from app.core.config import get_upstox_config

        with (
            patch.dict(
                os.environ,
                {
                    "UPSTOX_LIVE_API_KEY": "live-key",
                    "UPSTOX_LIVE_API_SECRET": "live-secret",
                    "UPSTOX_API_KEY": "old-key",
                    "UPSTOX_API_SECRET": "old-secret",
                },
                clear=True,
            ),
            patch("app.core.config.load_dotenv"),
        ):
            self.assertEqual(get_upstox_config()["client_id"], "live-key")

    def test_sandbox_token_is_isolated_even_for_same_app_and_user(self):
        from app.repositories.tokens import get_token, save_token

        connection = MagicMock()
        cursor = connection.cursor.return_value.__enter__.return_value
        cursor.fetchone.return_value = ("user", "private")
        with patch("app.repositories.tokens.get_connection", return_value=connection):
            save_token("app", "user", "private", environment="sandbox")
            sql, params = cursor.execute.call_args.args
            self.assertIn("environment", sql)
            self.assertIn("sandbox", params)
            get_token("app", "user", environment="sandbox")
            sql, params = cursor.execute.call_args.args
            self.assertIn("environment", sql)
            self.assertEqual(params, ("app", "user", "sandbox"))

    def test_invalid_environment_fails_before_database_access(self):
        from app.repositories.tokens import get_token

        with patch("app.repositories.tokens.get_connection") as connect:
            with self.assertRaises(ValueError):
                get_token("app", "user", environment="paper")
            connect.assert_not_called()

    def test_migration_preserves_existing_live_tokens_and_adds_audit(self):
        output = io.StringIO()
        command.upgrade(Config("alembic.ini", output_buffer=output), "head", sql=True)
        sql = output.getvalue()
        self.assertIn("DEFAULT 'live'", sql)
        self.assertIn("PRIMARY KEY (client_id, user_id, environment)", sql)
        self.assertIn("CREATE TABLE sandbox_order_tests", sql)

    def test_live_worker_uses_prefixed_app_key(self):
        from app.core.trading import load_trading_settings
        from app.repositories.trading_config import TradingConfig

        with (
            patch.dict(os.environ, {"UPSTOX_LIVE_API_KEY": "live-key"}, clear=True),
            patch("app.core.trading.load_dotenv"),
            patch("app.core.trading.get_trading_config", return_value=TradingConfig()),
        ):
            self.assertEqual(load_trading_settings().client_id, "live-key")


class SandboxTests(unittest.TestCase):
    def request(self):
        from app.services.sandbox import SandboxOrder

        return SandboxOrder("NSE_EQ|INE002A01018", 1, Decimal(10), Decimal("0.05"))

    def test_sdk_transport_can_only_reach_sandbox_and_never_retries(self):
        from app.services.sandbox import sandbox_client

        client = sandbox_client("private")
        try:
            self.assertTrue(client.configuration.sandbox)
            self.assertEqual(
                client.configuration.host, "https://api-sandbox.upstox.com"
            )
            self.assertEqual(
                client.configuration.order_host, "https://api-sandbox.upstox.com"
            )
            self.assertIs(
                client.rest_client.pool_manager.connection_pool_kw["retries"], False
            )
            with self.assertRaises(ValueError):
                client.call_api("/v2/user/profile", "GET")
        finally:
            client.pool.close()
            client.pool.join()
            client.rest_client.pool_manager.clear()

    def test_validation_enforces_quantity_notional_and_tick(self):
        from app.services.sandbox import SandboxOrder

        for quantity, price, tick in [
            (0, "10", ".05"),
            (2, "10", ".05"),
            (1, "1001", ".05"),
            (1, "10.01", ".05"),
            (1, "NaN", ".05"),
        ]:
            with (
                self.subTest(quantity=quantity, price=price),
                self.assertRaises(ValueError),
            ):
                SandboxOrder(
                    "NSE_EQ|INE002A01018", quantity, Decimal(price), Decimal(tick)
                ).validate(max_quantity=1, max_notional=Decimal(1000))

    def test_success_is_audited_before_place_and_cancel(self):
        from app.services.sandbox import run_order_test

        events = []
        audit = MagicMock()
        audit.start.side_effect = lambda *a: events.append("persist") or 7
        api = MagicMock()
        api.place_order.side_effect = lambda *a, **kw: (
            events.append("place")
            or SimpleNamespace(
                status="success", data=SimpleNamespace(order_id="order-1")
            )
        )
        api.cancel_order.side_effect = lambda *a, **kw: (
            events.append("cancel") or SimpleNamespace(status="success")
        )
        result = run_order_test(
            self.request(),
            api,
            audit,
            "app",
            "user",
            max_quantity=1,
            max_notional=Decimal(1000),
        )
        self.assertEqual(events, ["persist", "place", "cancel"])
        self.assertEqual(result, "cancel_acknowledged")
        self.assertEqual(
            audit.update.call_args.args, (7, "cancel_acknowledged", "order-1")
        )

    def test_timeout_is_persisted_without_retry_or_cancellation(self):
        from app.services.sandbox import run_order_test

        api, audit = MagicMock(), MagicMock()
        audit.start.return_value = 7
        api.place_order.side_effect = TimeoutError("private-token")
        result = run_order_test(
            self.request(),
            api,
            audit,
            "app",
            "user",
            max_quantity=1,
            max_notional=Decimal(1000),
        )
        self.assertEqual(result, "submission_uncertain")
        self.assertEqual(api.place_order.call_count, 1)
        api.cancel_order.assert_not_called()
        self.assertNotIn("private-token", str(audit.update.call_args))

    def test_database_failure_prevents_broker_submission(self):
        from app.services.sandbox import run_order_test

        api, audit = MagicMock(), MagicMock()
        audit.start.side_effect = OSError("private")
        with self.assertRaises(OSError):
            run_order_test(
                self.request(),
                api,
                audit,
                "app",
                "user",
                max_quantity=1,
                max_notional=Decimal(1000),
            )
        api.place_order.assert_not_called()


class SandboxCliTests(unittest.TestCase):
    def test_import_stores_only_sandbox_and_does_not_call_broker(self):
        from app.cli.sandbox import main

        with (
            patch(
                "app.cli.sandbox.get_sandbox_config",
                return_value={"client_id": "app", "user_id": "sandbox"},
            ),
            patch("app.cli.sandbox.getpass", return_value="private-token"),
            patch("app.cli.sandbox.save_token") as save,
            patch("app.cli.sandbox.sandbox_client") as client,
        ):
            self.assertEqual(main(["import-token"]), 0)
        save.assert_called_once_with(
            "app", "sandbox", "private-token", environment="sandbox"
        )
        client.assert_not_called()

    def test_missing_token_prevents_any_order(self):
        from app.cli.sandbox import main

        with (
            patch(
                "app.cli.sandbox.get_sandbox_config",
                return_value={"client_id": "app", "user_id": "sandbox"},
            ),
            patch("app.cli.sandbox.get_token", return_value=None),
            patch("app.cli.sandbox.get_latest_token", return_value=None),
            patch("app.cli.sandbox.sandbox_client") as client,
        ):
            self.assertEqual(
                main(
                    [
                        "test-order",
                        "--instrument",
                        "NSE_EQ|INE002A01018",
                        "--price",
                        "10",
                        "--tick-size",
                        ".05",
                    ]
                ),
                2,
            )
        client.assert_not_called()

    def test_partial_live_configuration_never_borrows_legacy_secret(self):
        from app.core.config import get_upstox_config

        with (
            patch.dict(
                os.environ,
                {
                    "UPSTOX_LIVE_API_KEY": "new",
                    "UPSTOX_API_KEY": "old",
                    "UPSTOX_API_SECRET": "old-secret",
                },
                clear=True,
            ),
            patch("app.core.config.load_dotenv"),
            self.assertRaises(ValueError),
        ):
            get_upstox_config()

    def test_cancellation_timeout_does_not_retry(self):
        from app.services.sandbox import run_order_test

        api, audit = MagicMock(), MagicMock()
        audit.start.return_value = 8
        api.place_order.return_value = SimpleNamespace(
            status="success", data=SimpleNamespace(order_id="order-1")
        )
        api.cancel_order.side_effect = TimeoutError("private")
        request = SandboxTests().request()
        self.assertEqual(
            run_order_test(
                request,
                api,
                audit,
                "app",
                "user",
                max_quantity=1,
                max_notional=Decimal(1000),
            ),
            "cancellation_uncertain",
        )
        self.assertEqual(api.cancel_order.call_count, 1)
        self.assertEqual(
            audit.update.call_args.args, (8, "cancellation_uncertain", "order-1")
        )


class SandboxFormTests(unittest.TestCase):
    def request(self, token="private-token", csrf="csrf"):
        import asyncio
        from datetime import datetime
        from urllib.parse import urlencode

        from app.main import app
        from app.repositories.sessions import Session
        from app.services.auth import require_session
        from tests.http import asgi_request

        app.dependency_overrides[require_session] = lambda: Session(
            1, "admin", "csrf", datetime(2099, 1, 1)
        )
        try:
            return asyncio.run(
                asgi_request(
                    "/auth/upstox/sandbox/token",
                    method="POST",
                    body=urlencode(
                        {"csrf_token": csrf, "access_token": token}
                    ).encode(),
                    extra_headers=[
                        (b"content-type", b"application/x-www-form-urlencoded")
                    ],
                )
            )
        finally:
            app.dependency_overrides.clear()

    def test_csrf_rejection_never_saves_token(self):
        with patch("app.repositories.tokens.save_token") as save:
            self.assertEqual(self.request(csrf="wrong")[0], 403)
        save.assert_not_called()

    def test_authenticated_token_import_never_stores_as_live(self):
        with (
            patch(
                "app.core.config.get_sandbox_config",
                return_value={"client_id": "app", "user_id": "sandbox"},
            ),
            patch("app.repositories.tokens.save_token") as save,
        ):
            status, headers, body = self.request()
        self.assertEqual(status, 303)
        self.assertNotIn(b"private-token", body)
        self.assertEqual(headers["cache-control"], "no-store")
        save.assert_called_once_with(
            "app", "sandbox", "private-token", environment="sandbox"
        )

    def test_blank_token_is_rejected(self):
        with patch("app.repositories.tokens.save_token") as save:
            self.assertEqual(self.request(token=" ")[0], 400)
        save.assert_not_called()


class SandboxSetupTests(unittest.TestCase):
    def test_sandbox_setup_opens_dedicated_portal(self):
        import asyncio
        from datetime import datetime

        from app.main import app
        from app.repositories.sessions import Session
        from app.services.auth import require_session
        from tests.http import asgi_request

        app.dependency_overrides[require_session] = lambda: Session(
            1, "admin", "csrf", datetime(2099, 1, 1)
        )
        try:
            status, headers, _ = asyncio.run(asgi_request("/auth/upstox/sandbox/login"))
            self.assertEqual(status, 303)
            self.assertEqual(
                headers["location"], "https://account.upstox.com/developer/apps#sandbox"
            )
        finally:
            app.dependency_overrides.clear()


class SandboxSafetyTests(unittest.TestCase):
    def test_strategy_worker_never_treats_sandbox_as_live(self):
        from app.cli.run_strategy import build_components
        from app.core.trading import TradingSettings

        with patch("app.cli.run_strategy.get_token") as token:
            with self.assertRaises(ValueError):
                build_components(TradingSettings(mode="sandbox"))
            token.assert_not_called()

    def test_unresolved_sandbox_intent_blocks_another_submission(self):
        from app.repositories.sandbox_tests import SandboxTestRepository

        connection = MagicMock()
        cursor = connection.cursor.return_value.__enter__.return_value
        cursor.fetchone.return_value = (9,)
        with (
            patch(
                "app.repositories.sandbox_tests.get_connection", return_value=connection
            ),
            self.assertRaises(RuntimeError),
        ):
            SandboxTestRepository().start("app", "user", {"tag": "test"})
        self.assertEqual(cursor.execute.call_count, 1)
        connection.commit.assert_not_called()
        connection.rollback.assert_called_once()

    def test_callback_access_logs_exclude_authorization_code(self):
        import logging

        from app.core.logging import OAuthAccessFilter

        record = logging.LogRecord(
            "uvicorn.access",
            20,
            "",
            0,
            '%s - "%s %s HTTP/%s" %d',
            (
                "client",
                "GET",
                "/auth/upstox/callback?code=private-code&state=private-state",
                "1.1",
                303,
            ),
            None,
        )
        OAuthAccessFilter().filter(record)
        self.assertNotIn("private-code", record.getMessage())
        self.assertNotIn("private-state", record.getMessage())
