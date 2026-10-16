import asyncio
import importlib.util
import json
import os
import tempfile
import time
import unittest
from http.cookies import SimpleCookie
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from urllib.parse import parse_qs, urlencode, urlsplit

import pymysql
from upstox_client.rest import ApiException

from app import config
from app.main import app


async def asgi_request(path, query="", cookie=""):
    messages = []

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        messages.append(message)

    async def ticker():
        # This sandbox cannot wake asyncio from worker threads without a timer.
        while True:
            await asyncio.sleep(0.01)

    tick = asyncio.create_task(ticker())
    try:
        await asyncio.wait_for(app({
            "type": "http", "asgi": {"version": "3.0", "spec_version": "2.4"},
            "http_version": "1.1", "method": "GET", "scheme": "http",
            "path": path, "raw_path": path.encode(), "query_string": query.encode(),
            "root_path": "", "headers": [(b"cookie", cookie.encode())] if cookie else [],
            "server": ("localhost", 8000), "client": ("127.0.0.1", 1),
        }, receive, send), timeout=5)
    finally:
        tick.cancel()
    start = next(m for m in messages if m["type"] == "http.response.start")
    headers = {k.decode(): v.decode() for k, v in start["headers"]}
    body = b"".join(m.get("body", b"") for m in messages if m["type"] == "http.response.body")
    return start["status"], headers, body


class UpstoxAuthTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.env_patch = patch.dict(os.environ, {
            "UPSTOX_API_KEY": "test-client", "UPSTOX_API_SECRET": "test-secret",
            "UPSTOX_REDIRECT_URI": "http://localhost:8000/auth/upstox/callback",
        }, clear=True)
        self.env_patch.start()
        self.addCleanup(self.env_patch.stop)
        env_file = patch.object(config, "ENV_FILE", Path(self.directory.name) / ".env")
        env_file.start()
        self.addCleanup(env_file.stop)

    def request(self, path, query="", cookie=""):
        return asyncio.run(asgi_request(path, query, cookie))

    def login(self):
        status, headers, _ = self.request("/auth/upstox/login")
        self.assertEqual(status, 303)
        query = parse_qs(urlsplit(headers["location"]).query)
        cookie = SimpleCookie()
        cookie.load(headers["set-cookie"])
        return query["state"][0], cookie.output(header="", sep=";").strip().split(";", 1)[0]

    def callback(self, state, cookie, **extra):
        return self.request("/auth/upstox/callback", urlencode({"code": "auth-code", "state": state, **extra}), cookie)

    def test_login_uses_configured_redirect_and_browser_state(self):
        status, headers, _ = self.request("/auth/upstox/login")
        self.assertEqual(status, 303)
        url = urlsplit(headers["location"])
        self.assertEqual((url.scheme, url.netloc, url.path), ("https", "api.upstox.com", "/v2/login/authorization/dialog"))
        query = parse_qs(url.query)
        self.assertEqual(query["client_id"], ["test-client"])
        self.assertEqual(query["redirect_uri"], ["http://localhost:8000/auth/upstox/callback"])
        self.assertEqual(query["response_type"], ["code"])
        self.assertTrue(query["state"][0])
        self.assertIn("HttpOnly", headers["set-cookie"])
        self.assertIn("SameSite=lax", headers["set-cookie"])
        self.assertNotIn("test-secret", headers["location"])

    def test_missing_credentials_produces_sanitized_configuration_error(self):
        os.environ.pop("UPSTOX_API_SECRET")
        status, _, body = self.request("/auth/upstox/login")
        self.assertEqual(status, 503)
        self.assertNotIn(b"test-client", body)

    def test_callback_requires_state_before_contacting_broker(self):
        with patch("upstox_client.LoginApi.token", side_effect=AssertionError("Broker must not be contacted")):
            status, _, _ = self.callback("untrusted", "")
        self.assertEqual(status, 400)

    def test_callback_saves_before_redirecting_home_without_exposing_token(self):
        state, cookie = self.login()
        self.assertIsNotNone(importlib.util.find_spec("app.upstox_auth"))
        events = []

        def exchange(api_version, **kwargs):
            self.assertEqual(api_version, "2.0")
            self.assertEqual(kwargs["code"], "auth-code")
            self.assertEqual(kwargs["client_id"], "test-client")
            self.assertEqual(kwargs["client_secret"], "test-secret")
            self.assertEqual(kwargs["redirect_uri"], "http://localhost:8000/auth/upstox/callback")
            self.assertEqual(kwargs["grant_type"], "authorization_code")
            self.assertGreater(kwargs["_request_timeout"], 0)
            events.append("exchange")
            return SimpleNamespace(user_id="TESTUSER", access_token="test-access-token")

        def save(client_id, user_id, token):
            self.assertEqual((client_id, user_id, token), ("test-client", "TESTUSER", "test-access-token"))
            events.append("save")

        with patch("upstox_client.LoginApi.token", side_effect=exchange), patch("app.upstox_auth.save_token", side_effect=save):
            status, headers, body = self.callback(state, cookie)
        self.assertEqual(status, 303)
        self.assertEqual(headers["location"], "/")
        self.assertNotIn(b"test-access-token", body)
        self.assertNotIn("test-access-token", " ".join(headers.values()))
        self.assertEqual(events, ["exchange", "save"])
        self.assertIn("no-store", headers["cache-control"])
        self.assertIn("Max-Age=0", headers["set-cookie"])

    def test_expired_state_is_rejected(self):
        with patch("time.time", return_value=time.time() - 700):
            state, cookie = self.login()
        status, _, _ = self.callback(state, cookie)
        self.assertEqual(status, 400)

    def test_matching_forged_cookie_and_state_are_rejected(self):
        status, _, _ = self.callback("forged", "upstox_oauth_state=forged")
        self.assertEqual(status, 400)

    def test_missing_code_and_user_denial_do_not_exchange_token(self):
        state, cookie = self.login()
        with patch("upstox_client.LoginApi.token", side_effect=AssertionError("No code to exchange")):
            for params in ({"state": state}, {"state": state, "error": "access_denied"}):
                status, _, _ = self.request("/auth/upstox/callback", urlencode(params), cookie)
                self.assertEqual(status, 400)

    def test_broker_error_is_sanitized(self):
        state, cookie = self.login()
        with patch("upstox_client.LoginApi.token", side_effect=ApiException(status=400, reason="test-secret")):
            status, headers, body = self.callback(state, cookie)
        self.assertEqual(status, 502)
        self.assertNotIn(b"test-secret", body)
        self.assertIn("no-store", headers["cache-control"])

    def test_missing_token_or_user_id_is_rejected(self):
        for result in (SimpleNamespace(access_token="", user_id="TESTUSER"), SimpleNamespace(access_token="test-access-token", user_id=None)):
            state, cookie = self.login()
            with patch("upstox_client.LoginApi.token", return_value=result):
                status, _, _ = self.callback(state, cookie)
            self.assertEqual(status, 502)

    def test_storage_failure_does_not_return_token(self):
        state, cookie = self.login()
        result = SimpleNamespace(user_id="TESTUSER", access_token="test-access-token")
        with patch("upstox_client.LoginApi.token", return_value=result), patch("app.upstox_auth.save_token", side_effect=pymysql.err.OperationalError("private database details")):
            status, _, body = self.callback(state, cookie)
        self.assertEqual(status, 503)
        self.assertNotIn(b"test-access-token", body)
        self.assertNotIn(b"private database details", body)

    def test_database_failures_explain_the_action_without_exposing_secrets(self):
        cases = (
            (pymysql.err.ProgrammingError(1146, "private database details test-access-token"), "uv run alembic upgrade head"),
            (pymysql.err.OperationalError(1045, "private database details test-access-token"), "MYSQL_USER"),
            (pymysql.err.OperationalError(1049, "private database details test-access-token"), "MYSQL_DATABASE"),
            (pymysql.err.OperationalError(2003, "private database details test-access-token"), "MYSQL_HOST"),
            (pymysql.err.OperationalError(1142, "private database details test-access-token"), "INSERT and UPDATE"),
            (RuntimeError("'cryptography' package is required for sha256_password or caching_sha2_password auth methods"), "uv add 'pymysql[rsa]'"),
        )
        for error, instruction in cases:
            with self.subTest(error=type(error).__name__, instruction=instruction):
                state, cookie = self.login()
                result = SimpleNamespace(user_id="TESTUSER", access_token="test-access-token")
                with patch("upstox_client.LoginApi.token", return_value=result), patch("app.upstox_auth.save_token", side_effect=error):
                    with self.assertLogs("app.upstox_auth", level="ERROR") as logs:
                        status, headers, body = self.callback(state, cookie)
                self.assertEqual(status, 503)
                self.assertIn(instruction, json.loads(body)["detail"])
                self.assertIn("no-store", headers["cache-control"])
                for secret in ("private database details", "test-access-token", "test-secret"):
                    self.assertNotIn(secret, body.decode())
                    self.assertNotIn(secret, " ".join(logs.output))
