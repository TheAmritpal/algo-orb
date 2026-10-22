import asyncio
import unittest
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import patch

import pymysql
from upstox_client.rest import ApiException

from app.main import app
from app.repositories.sessions import Session
from app.services import upstox
from app.services.auth import require_session
from tests.http import asgi_request


class UpstoxStatusTests(unittest.TestCase):
    def setUp(self):
        from app.repositories.tokens import UpstoxToken

        self.stored = UpstoxToken("BROKER1", "private-token")
        patches = [
            patch.object(
                upstox,
                "get_upstox_config",
                return_value={
                    "client_id": "client",
                    "client_secret": "secret",
                    "redirect_uri": "http://localhost/callback",
                },
            ),
            patch("app.services.upstox.get_latest_token", return_value=self.stored),
            patch("app.services.upstox.upstox_client.ApiClient"),
            patch("app.services.upstox.upstox_client.UserApi"),
        ]
        self.config, self.lookup, self.client, self.user_api = [
            p.start() for p in patches
        ]
        for p in patches:
            self.addCleanup(p.stop)
        self.user_api.return_value.get_profile.return_value = SimpleNamespace(
            status="success", data=SimpleNamespace(user_id="BROKER1")
        )

    def test_valid_saved_token_is_connected_and_transport_closed(self):
        result = upstox.connection_status()
        self.assertEqual(result.state, "connected")
        self.assertEqual(result.user_id, "BROKER1")
        self.lookup.assert_called_once_with("client")
        self.assertEqual(self.client.call_args.args[0].access_token, "private-token")
        self.user_api.return_value.get_profile.assert_called_once_with(
            "2.0", _request_timeout=(3, 5)
        )
        self.client.return_value.rest_client.pool_manager.clear.assert_called_once()
        self.client.return_value.pool.close.assert_called_once()
        self.client.return_value.pool.join.assert_called_once()
        self.assertNotIn("private-token", repr(result))

    def test_missing_token_is_disconnected_without_broker_call(self):
        self.lookup.return_value = None
        self.assertEqual(upstox.connection_status().state, "disconnected")
        self.user_api.assert_not_called()

    def test_expired_token_is_disconnected_but_not_deleted(self):
        self.user_api.return_value.get_profile.side_effect = ApiException(
            status=401, reason="private-token"
        )
        result = upstox.connection_status()
        self.assertEqual(result.state, "disconnected")
        self.assertNotIn("private-token", repr(result))
        self.client.return_value.pool.join.assert_called_once()

    def test_temporary_failure_is_unavailable_not_disconnected(self):
        for error in (
            ApiException(status=429),
            ApiException(status=500),
            OSError("private-token"),
        ):
            self.user_api.return_value.get_profile.side_effect = error
            result = upstox.connection_status()
            self.assertEqual(result.state, "unavailable")
            self.assertNotIn("private-token", repr(result))

    def test_wrong_account_and_incomplete_profile_are_not_connected(self):
        for profile in (
            SimpleNamespace(status="success", data=SimpleNamespace(user_id="OTHER")),
            SimpleNamespace(status="success", data=None),
            SimpleNamespace(status="error", data=SimpleNamespace(user_id="BROKER1")),
        ):
            self.user_api.return_value.get_profile.return_value = profile
            self.assertEqual(upstox.connection_status().state, "unavailable")

    def test_database_and_configuration_errors_are_sanitized(self):
        self.lookup.side_effect = pymysql.OperationalError("private-token")
        self.assertEqual(upstox.connection_status().state, "unavailable")
        self.user_api.assert_not_called()
        self.config.side_effect = ValueError("private-token")
        self.assertNotIn("private-token", repr(upstox.connection_status()))

    def test_dashboard_renders_status_and_only_connects_when_disconnected(self):
        from app.services.strategy_status import StrategyStatus

        strategy = patch("app.routers.pages.get_strategy_status", return_value=StrategyStatus())
        strategy.start()
        self.addCleanup(strategy.stop)
        app.dependency_overrides[require_session] = lambda: Session(
            7,
            "admin",
            "csrf",
            datetime.now(UTC).replace(tzinfo=None) + timedelta(hours=1),
        )
        self.addCleanup(app.dependency_overrides.clear)
        for state, label in (
            ("connected", b"Connected"),
            ("disconnected", b"Disconnected"),
            ("unavailable", b"Status unavailable"),
        ):
            with (
                self.subTest(state=state),
                patch(
                    "app.routers.pages.connection_status",
                    return_value=upstox.ConnectionStatus(
                        state, user_id="BROKER1" if state == "connected" else None
                    ),
                ),
            ):
                status, headers, body = asyncio.run(asgi_request("/"))
                self.assertEqual(status, 200)
                self.assertIn(label, body)
                self.assertEqual(
                    b'href="/auth/upstox/login"' in body, state == "disconnected"
                )
                self.assertEqual(
                    b'action="/auth/upstox/disconnect"' in body, state == "connected"
                )
                self.assertNotIn(b"private-token", body)
                self.assertEqual(headers["cache-control"], "no-store")
