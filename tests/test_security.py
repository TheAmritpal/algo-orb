import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.core import config


class SecurityTests(unittest.TestCase):
    def setUp(self):
        from app.core import security

        self.security = security

    def test_salted_passwords_and_verification(self):
        s = self.security
        encoded = s.hash_password("correct horse battery")
        self.assertNotEqual(encoded, s.hash_password("correct horse battery"))
        self.assertNotIn("correct horse battery", encoded)
        self.assertTrue(s.verify_password("correct horse battery", encoded))
        self.assertFalse(s.verify_password("wrong", encoded))
        self.assertFalse(s.verify_password("wrong", None))
        self.assertFalse(s.verify_password("wrong", "broken-hash"))

    def test_tokens_are_random_and_stored_as_sha256(self):
        s = self.security
        token = s.new_token()
        self.assertNotEqual(token, s.new_token())
        self.assertEqual(len(token), 43)
        self.assertEqual(
            s.hash_token("abc"),
            "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad",
        )

    def test_csrf_rejects_tampering_expiry_and_future(self):
        s = self.security
        with patch("time.time", return_value=1000):
            token = s.new_login_csrf("secret")
            self.assertTrue(s.valid_login_csrf(token, token, "secret"))
            self.assertFalse(s.valid_login_csrf(token, token, "other-secret"))
            self.assertFalse(s.valid_login_csrf(token, "other", "secret"))
            self.assertFalse(s.valid_login_csrf("ø", "ø", "secret"))
        for now in (999, 1601):
            with patch("time.time", return_value=now):
                self.assertFalse(s.valid_login_csrf(token, token, "secret"))

    def test_auth_configuration_fails_closed(self):
        with (
            tempfile.TemporaryDirectory() as directory,
            patch.object(config, "ENV_FILE", Path(directory) / ".env"),
            patch.dict(os.environ, {}, clear=True),
        ):
            with self.assertRaises(ValueError):
                config.get_auth_settings()
            os.environ["APP_SESSION_SECRET"] = "x" * 32
            self.assertTrue(config.get_auth_settings().cookie_secure)
            os.environ["APP_COOKIE_SECURE"] = "false"
            self.assertFalse(config.get_auth_settings().cookie_secure)
            os.environ["APP_COOKIE_SECURE"] = "maybe"
            with self.assertRaises(ValueError):
                config.get_auth_settings()
            os.environ["APP_USERNAME"] = "admin"
            os.environ["APP_PASSWORD"] = "replace_with_your_password"
            with self.assertRaises(ValueError):
                config.get_admin_credentials()
            os.environ["APP_PASSWORD"] = "long-test-password"
            self.assertEqual(
                config.get_admin_credentials(), ("admin", "long-test-password")
            )
