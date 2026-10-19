import asyncio
import unittest
from datetime import datetime
from unittest.mock import patch

from app.main import app
from app.repositories.sessions import Session
from app.services.auth import require_session
from tests.http import asgi_request


class TradingConfigUITests(unittest.TestCase):
    def setUp(self):
        app.dependency_overrides[require_session] = lambda: Session(
            1, "admin", "csrf", datetime(2030, 1, 1)
        )
        self.addCleanup(app.dependency_overrides.clear)

    def submit(self, fields):
        return asyncio.run(
            asgi_request(
                "/strategy/config",
                method="POST",
                body=fields.encode(),
                extra_headers=[(b"content-type", b"application/x-www-form-urlencoded")],
            )
        )

    def test_save_and_csrf_validation(self):
        with patch("app.repositories.trading_config.get_connection") as connect:
            cursor = connect.return_value.cursor.return_value.__enter__.return_value
            cursor.fetchone.return_value = (1,)
            status, headers, _ = self.submit("csrf_token=csrf&mode=paper")
            self.assertEqual(status, 303)
            self.assertEqual(headers["location"], "/?config_saved=1")
            connect.return_value.commit.assert_called_once()
            connect.reset_mock()
            status, _, _ = self.submit("csrf_token=wrong&mode=live&live_enabled=true")
            self.assertEqual(status, 403)
            connect.assert_not_called()

    def test_reject_invalid_configuration_and_hide_database_errors(self):
        for fields in (
            "mode=sandbox&live_enabled=false",
            "mode=live&live_enabled=yes",
            "mode=live&live_enabled=false",
        ):
            status, _, _ = self.submit("csrf_token=csrf&" + fields)
            self.assertEqual(status, 400)
        with patch(
            "app.repositories.trading_config.get_connection",
            side_effect=RuntimeError("secret"),
        ):
            status, _, body = self.submit(
                "csrf_token=csrf&mode=paper&live_enabled=false"
            )
        self.assertEqual(status, 503)
        self.assertNotIn(b"secret", body)
