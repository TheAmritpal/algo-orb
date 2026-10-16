import asyncio
import os
import re
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from http.cookies import SimpleCookie
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from urllib.parse import urlencode

import pymysql

from app.core import config, security
from app.main import app
from app.repositories.sessions import Session
from app.repositories.users import User
from app.services import auth
from tests.http import asgi_request


def cookie_value(headers, name):
    for header in headers.getlist("set-cookie"):
        cookie = SimpleCookie()
        cookie.load(header)
        if name in cookie:
            return cookie[name]
    raise AssertionError(f"missing cookie: {name}")


class AuthTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.user = User(7, "admin", security.hash_password("long-test-password"))

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        patches = [
            patch.dict(
                os.environ,
                {"APP_SESSION_SECRET": "x" * 32, "APP_COOKIE_SECURE": "false"},
                clear=True,
            ),
            patch.object(config, "ENV_FILE", Path(self.directory.name) / ".env"),
            patch.object(auth, "limiter", auth.LoginLimiter()),
            patch("app.repositories.users.find_user", return_value=self.user),
            patch("app.repositories.sessions.create_session"),
            patch(
                "app.repositories.sessions.find_session",
                return_value=Session(
                    7,
                    "admin",
                    "session-csrf",
                    datetime.now(UTC).replace(tzinfo=None) + timedelta(hours=1),
                ),
            ),
            patch("app.repositories.sessions.delete_session"),
            patch(
                "app.routers.pages.connection_status",
                return_value=SimpleNamespace(state="disconnected", user_id=None),
            ),
        ]
        self.mocks = [p.start() for p in patches]
        for p in patches:
            self.addCleanup(p.stop)
        app.dependency_overrides.clear()

    def request(
        self, path, cookie="", method="GET", fields=None, body=None, headers=()
    ):
        if fields is not None:
            body = urlencode(fields).encode()
        if body is not None:
            headers = (
                *headers,
                (b"content-type", b"application/x-www-form-urlencoded"),
            )
        return asyncio.run(
            asgi_request(
                path,
                cookie=cookie,
                method=method,
                body=body or b"",
                extra_headers=headers,
            )
        )

    def challenge(self):
        status, headers, body = self.request("/login")
        self.assertEqual(status, 200)
        token = re.search(rb'name="csrf_token" value="([^"]+)"', body).group(1).decode()
        morsel = cookie_value(headers, "orb_login_csrf")
        self.assertEqual(morsel.value, token)
        return token, f"orb_login_csrf={morsel.value}"

    def login(self, username="admin", password="long-test-password"):
        token, cookie = self.challenge()
        return self.request(
            "/login",
            cookie,
            "POST",
            {"username": username, "password": password, "csrf_token": token},
        )

    def test_login_issues_hashed_session_and_cookie(self):
        status, headers, body = self.login()
        self.assertEqual(status, 303)
        self.assertEqual(headers["location"], "/")
        morsel = cookie_value(headers, "orb_session")
        self.assertEqual(morsel["max-age"], "28800")
        self.assertEqual(morsel["samesite"], "lax")
        self.assertTrue(morsel["httponly"])
        self.assertEqual(morsel["path"], "/")
        self.assertFalse(morsel["secure"])
        args = self.mocks[4].call_args.args
        self.assertEqual(args[0], 7)
        self.assertEqual(args[4], self.user.password_hash)
        self.assertEqual(args[1], security.hash_token(morsel.value))
        self.assertNotIn(morsel.value.encode(), body)
        self.assertEqual(cookie_value(headers, "orb_login_csrf")["max-age"], "0")
        self.assertEqual(headers["cache-control"], "no-store")

    def test_password_rotated_during_login_returns_generic_error(self):
        from app.repositories.sessions import AuthenticationChanged

        self.mocks[4].side_effect = AuthenticationChanged()
        status, headers, body = self.login()
        self.assertEqual(status, 401)
        self.assertIn(b"Invalid username or password", body)
        self.assertFalse(
            any("orb_session=" in value for value in headers.getlist("set-cookie"))
        )

    def test_https_cookie_setting_and_login_csrf(self):
        os.environ["APP_COOKIE_SECURE"] = "true"
        _, headers, _ = self.login()
        self.assertTrue(cookie_value(headers, "orb_session")["secure"])
        status, _, _ = self.request(
            "/login",
            method="POST",
            fields={
                "username": "admin",
                "password": "long-test-password",
                "csrf_token": "forged",
            },
        )
        self.assertEqual(status, 403)

    def test_unknown_and_wrong_password_have_same_error(self):
        status, _, body = self.login(password="wrong")
        self.assertEqual(status, 401)
        self.mocks[3].return_value = None
        other_status, _, other_body = self.login(username="unknown", password="wrong")
        self.assertEqual(other_status, 401)
        self.assertIn(b"Invalid username or password", body)
        self.assertIn(b"Invalid username or password", other_body)
        self.assertNotIn(b"unknown", other_body)
        self.mocks[4].assert_not_called()

    def test_corrupt_password_hash_fails_closed(self):
        self.mocks[3].return_value = User(7, "admin", "broken-hash")
        self.assertEqual(self.login()[0], 401)
        self.mocks[4].assert_not_called()

    def test_protected_routes_block_before_broker(self):
        with patch(
            "upstox_client.LoginApi.token", side_effect=AssertionError("no broker")
        ):
            for path in ("/", "/auth/upstox/login", "/auth/upstox/callback"):
                status, headers, _ = self.request(path)
                self.assertEqual(status, 303)
                self.assertEqual(headers["location"], "/login")
                self.assertEqual(headers["cache-control"], "no-store")
        self.mocks[5].assert_not_called()

    def test_session_hash_lookup_dashboard_and_revocation(self):
        raw = "a" * 43
        status, _headers, body = self.request("/", cookie=f"orb_session={raw}")
        self.assertEqual(status, 200)
        self.assertIn(b"Connect Upstox", body)
        self.assertIn(b"session-csrf", body)
        self.assertIn(b"/logout", body)
        self.mocks[5].assert_called_with(security.hash_token(raw))
        self.mocks[5].return_value = None
        self.assertEqual(self.request("/", cookie=f"orb_session={raw}")[0], 303)

    def test_expired_session_and_malformed_token_rejected(self):
        self.mocks[5].return_value = Session(7, "admin", "csrf", datetime(2000, 1, 1))
        with patch(
            "upstox_client.LoginApi.token", side_effect=AssertionError("no broker")
        ):
            self.assertEqual(
                self.request("/auth/upstox/callback", cookie="orb_session=" + "a" * 43)[
                    0
                ],
                303,
            )
        self.mocks[5].reset_mock()
        self.assertEqual(self.request("/", cookie="orb_session=" + "a" * 5000)[0], 303)
        self.mocks[5].assert_not_called()

    def test_logout_requires_csrf_then_deletes_session(self):
        cookie = "orb_session=" + "a" * 43
        self.assertEqual(
            self.request("/logout", cookie, "POST", fields={"csrf_token": "wrong"})[0],
            403,
        )
        self.mocks[6].assert_not_called()
        status, headers, _ = self.request(
            "/logout", cookie, "POST", fields={"csrf_token": "session-csrf"}
        )
        self.assertEqual(status, 303)
        self.assertEqual(headers["location"], "/login")
        self.mocks[6].assert_called_with(security.hash_token("a" * 43))
        self.assertEqual(cookie_value(headers, "orb_session")["max-age"], "0")
        self.assertEqual(self.request("/logout", cookie)[0], 405)

    def test_database_errors_and_missing_config_are_sanitized(self):
        self.mocks[3].side_effect = pymysql.OperationalError("sensitive-password")
        status, _, body = self.login()
        self.assertEqual(status, 503)
        self.assertNotIn(b"sensitive-password", body)
        self.mocks[5].side_effect = pymysql.OperationalError("sensitive-password")
        self.assertEqual(self.request("/", cookie="orb_session=" + "a" * 43)[0], 503)
        os.environ.pop("APP_SESSION_SECRET")
        self.assertEqual(self.request("/login")[0], 503)
        self.assertEqual(self.request("/", cookie="orb_session=" + "a" * 43)[0], 503)
        self.assertEqual(self.request("/health")[0], 200)

    def test_bad_forms_rejected_before_password_work(self):
        token, cookie = self.challenge()
        for body, wanted in (
            (b"a" * 8193, 413),
            (b"username=%FF", 400),
            (b"username=a&username=b", 400),
            (b"", 400),
        ):
            status, _, _ = self.request("/login", cookie, "POST", body=body)
            self.assertEqual(status, wanted)
        for username, password in (
            ("a" * 129, "pw"),
            ("admin", "a" * 1025),
            ("", "pw"),
        ):
            self.assertEqual(
                self.request(
                    "/login",
                    cookie,
                    "POST",
                    fields={
                        "username": username,
                        "password": password,
                        "csrf_token": token,
                    },
                )[0],
                400,
            )
        self.mocks[3].assert_not_called()

    def test_http_rate_limit_ignores_forwarded_headers(self):
        for _ in range(5):
            self.assertEqual(self.login(password="wrong")[0], 401)
        token, cookie = self.challenge()
        status, headers, _ = self.request(
            "/login",
            cookie,
            "POST",
            fields={
                "username": "admin",
                "password": "long-test-password",
                "csrf_token": token,
            },
            headers=((b"x-forwarded-for", b"203.0.113.1"),),
        )
        self.assertEqual(status, 429)
        self.assertIn("retry-after", headers)

    def test_public_assets_and_health(self):
        self.assertEqual(self.request("/health")[2], b'{"status":"ok"}')
        self.assertEqual(self.request("/static/app.css")[0], 200)
        for path in ("/docs", "/redoc", "/openapi.json"):
            self.assertEqual(self.request(path)[0], 404)


class LimiterTests(unittest.TestCase):
    def test_window_and_capacity(self):
        limiter = auth.LoginLimiter()
        with patch("time.monotonic", return_value=1000):
            for _ in range(5):
                self.assertTrue(limiter.allow("client"))
                limiter.failed("client")
            self.assertFalse(limiter.allow("client"))
            for n in range(9999):
                limiter.failed(str(n))
            self.assertFalse(limiter.allow("new-client"))
        with patch("time.monotonic", return_value=1301):
            self.assertTrue(limiter.allow("client"))
            self.assertTrue(limiter.allow("new-client"))
