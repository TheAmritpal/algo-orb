import asyncio
import unittest
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pymysql
from upstox_client.rest import ApiException

from app.main import app
from app.repositories.sessions import Session
from app.repositories.tokens import UpstoxToken
from app.services.auth import require_session
from tests.http import asgi_request


class DisconnectTests(unittest.TestCase):
    def setUp(self):
        self.stack = []
        for target, kwargs in [
            (
                "app.services.upstox_disconnect.get_upstox_config",
                {"return_value": {"client_id": "app"}},
            ),
            (
                "app.services.upstox_disconnect.get_token",
                {"return_value": UpstoxToken("U1", "SECRET")},
            ),
            ("app.services.upstox_disconnect.delete_token", {}),
            ("app.services.upstox_disconnect.WorkerLock", {}),
            ("app.services.upstox_disconnect.StrategyRepository", {}),
            ("app.services.upstox_disconnect.upstox_client.ApiClient", {}),
            ("app.services.upstox_disconnect.upstox_client.LoginApi", {}),
        ]:
            item = patch(target, **kwargs)
            self.stack.append(item.start())
            self.addCleanup(item.stop)
        (
            self.config,
            self.get,
            self.delete,
            self.lock,
            self.repo,
            self.client,
            self.api,
        ) = self.stack
        self.lock.return_value.acquire.return_value = True
        self.repo.return_value.list_unresolved.return_value = []
        self.api.return_value.logout.return_value = SimpleNamespace(
            status="success", data=True
        )
        app.dependency_overrides[require_session] = lambda: Session(
            1,
            "admin",
            "csrf",
            datetime.now(UTC).replace(tzinfo=None) + timedelta(hours=1),
        )
        self.addCleanup(app.dependency_overrides.clear)

    def request(self, csrf="csrf"):
        return asyncio.run(
            asgi_request(
                "/auth/upstox/disconnect",
                method="POST",
                body=f"csrf_token={csrf}&user_id=U1".encode(),
                extra_headers=[(b"content-type", b"application/x-www-form-urlencoded")],
            )
        )

    def test_success_revokes_and_removes_exact_live_token(self):
        status, headers, _ = self.request()
        self.assertEqual(status, 303)
        self.assertEqual(headers["location"], "/")
        self.api.return_value.logout.assert_called_once_with(
            "2.0", _request_timeout=(3, 5)
        )
        self.delete.assert_called_once_with("app", "U1", "SECRET", environment="live")
        self.client.return_value.pool.join.assert_called_once()
        self.assertEqual(self.lock.return_value.close.call_count, 2)

    def test_csrf_failure_does_not_touch_broker(self):
        self.assertEqual(self.request("bad")[0], 403)
        self.api.assert_not_called()
        self.delete.assert_not_called()

    def test_requires_website_login(self):
        app.dependency_overrides.clear()
        with patch("app.services.auth.get_auth_settings", side_effect=ValueError):
            self.assertNotEqual(self.request()[0], 303)
        self.api.assert_not_called()

    def test_running_worker_blocks_disconnect(self):
        self.lock.return_value.acquire.return_value = False
        self.assertEqual(self.request()[0], 409)
        self.api.assert_not_called()
        self.delete.assert_not_called()
        self.lock.return_value.close.assert_called()

    def test_live_worker_blocks_after_paper_lock_is_acquired(self):
        paper, live = MagicMock(), MagicMock()
        paper.acquire.return_value = True
        live.acquire.return_value = False
        self.lock.side_effect = [paper, live]
        self.assertEqual(self.request()[0], 409)
        self.assertEqual(
            [call.args[2] for call in self.lock.call_args_list], ["paper", "live"]
        )
        self.api.assert_not_called()
        self.delete.assert_not_called()
        paper.close.assert_called_once()
        live.close.assert_called_once()

    def test_unresolved_live_exposure_blocks_disconnect(self):
        self.repo.return_value.list_unresolved.return_value = [object()]
        self.assertEqual(self.request()[0], 409)
        self.api.assert_not_called()
        self.delete.assert_not_called()

    def test_broker_failure_retains_token_and_hides_secret(self):
        self.api.return_value.logout.side_effect = ApiException(
            status=500, reason="SECRET"
        )
        status, _, body = self.request()
        self.assertEqual(status, 502)
        self.assertNotIn(b"SECRET", body)
        self.delete.assert_not_called()

    def test_expired_token_can_be_removed(self):
        self.api.return_value.logout.side_effect = ApiException(status=401)
        self.assertEqual(self.request()[0], 303)
        self.delete.assert_called_once()

    def test_incomplete_logout_is_not_success(self):
        self.api.return_value.logout.return_value = SimpleNamespace(
            status="success", data=False
        )
        self.assertEqual(self.request()[0], 502)
        self.delete.assert_not_called()

    def test_database_failure_is_sanitized(self):
        self.delete.side_effect = pymysql.OperationalError(2006, "SECRET")
        status, _, body = self.request()
        self.assertEqual(status, 503)
        self.assertNotIn(b"SECRET", body)


class DeleteTokenTests(unittest.TestCase):
    def test_delete_is_exact_and_conditional_on_token(self):
        from app.repositories import tokens

        connection = MagicMock()
        with patch.object(tokens, "get_connection", return_value=connection):
            tokens.delete_token("app", "U1", "SECRET", environment="live")
        cursor = connection.cursor.return_value.__enter__.return_value
        sql, params = cursor.execute.call_args.args
        self.assertIn("DELETE FROM upstox_tokens", sql)
        self.assertIn("access_token", sql)
        self.assertEqual(params, ("app", "U1", "live", "SECRET"))
        connection.commit.assert_called_once()
        connection.close.assert_called_once()

    def test_failed_delete_rolls_back_without_leaking_or_losing_connection(self):
        from app.repositories import tokens

        connection = MagicMock()
        cursor = connection.cursor.return_value.__enter__.return_value
        cursor.execute.side_effect = pymysql.OperationalError(2006, "SECRET")
        with (
            patch.object(tokens, "get_connection", return_value=connection),
            self.assertRaises(pymysql.OperationalError),
        ):
            tokens.delete_token("app", "U1", "SECRET")
        connection.rollback.assert_called_once()
        connection.commit.assert_not_called()
        connection.close.assert_called_once()
