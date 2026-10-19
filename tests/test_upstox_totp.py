import io
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from upstox_client.rest import ApiException

from app.core import config
from app.repositories.worker_lock import WorkerLock


class TotpTests(unittest.TestCase):
    def test_rfc6238_sha1_vectors_truncated_to_six_digits(self):
        from app.services.upstox_totp import generate_totp

        secret = "GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ"
        for timestamp, expected in [
            (59, "287082"),
            (1111111109, "081804"),
            (1111111111, "050471"),
            (1234567890, "005924"),
            (2000000000, "279037"),
            (20000000000, "353130"),
        ]:
            self.assertEqual(generate_totp(secret, timestamp), expected)

    def test_configuration_rejects_six_digit_code_as_setup_secret(self):
        from app.services.upstox_totp import load_settings

        with (
            tempfile.TemporaryDirectory() as directory,
            patch.object(config, "ENV_FILE", Path(directory) / ".env"),
            patch.dict(
                os.environ,
                {
                    "UPSTOX_API_KEY": "app",
                    "ORB_BROKER_USER_ID": "U1",
                    "UPSTOX_TOTP_PIN": "012345",
                    "UPSTOX_TOTP_SECRET": "123456",
                },
                clear=True,
            ),
            self.assertRaises(ValueError),
        ):
            load_settings()

    def test_configuration_preserves_pin_zeros_and_needs_no_oauth_secret(self):
        from app.services.upstox_totp import load_settings

        with (
            tempfile.TemporaryDirectory() as directory,
            patch.object(config, "ENV_FILE", Path(directory) / ".env"),
            patch.dict(
                os.environ,
                {
                    "UPSTOX_API_KEY": "app",
                    "ORB_BROKER_USER_ID": "U1",
                    "UPSTOX_TOTP_PIN": "012345",
                    "UPSTOX_TOTP_SECRET": "GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ",
                },
                clear=True,
            ),
        ):
            settings = load_settings()
        self.assertEqual(settings.pin, "012345")
        self.assertEqual(settings.client_id, "app")
        self.assertNotIn("GEZDG", repr(settings))


class RetrievalTests(unittest.TestCase):
    def setUp(self):
        from app.services import upstox_totp

        self.service = upstox_totp
        self.settings = upstox_totp.TotpSettings(
            "app", "U1", "012345", "GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ"
        )
        patches = [
            patch.object(upstox_totp, "load_settings", return_value=self.settings),
            patch.object(upstox_totp, "WorkerLock", spec=WorkerLock),
            patch.object(upstox_totp, "get_token", return_value=None),
            patch.object(upstox_totp, "save_token"),
            patch.object(upstox_totp.sdk, "ApiClient"),
            patch.object(upstox_totp.sdk, "UserApi"),
        ]
        self.load, self.lock, self.get, self.save, self.client, self.profile = [
            p.start() for p in patches
        ]
        for p in patches:
            self.addCleanup(p.stop)
        self.client.return_value.rest_client.pool_manager.connection_pool_kw = {}
        self.lock.return_value.acquire.return_value = True
        self.client.return_value.call_api.return_value = {
            "user_id": "U1",
            "access_token": "SECRET",
        }
        self.profile.return_value.get_profile.return_value = SimpleNamespace(
            status="success", data=SimpleNamespace(user_id="U1")
        )

    def test_initial_connect_saves_verified_token_via_sdk_without_exposure(self):
        self.assertEqual(self.service.retrieve_token(connect=True, now=59), "saved")
        self.save.assert_called_once_with("app", "U1", "SECRET", environment="live")
        args, kwargs = self.client.return_value.call_api.call_args
        self.assertEqual(args, ("/v2/totp-login/authorization/token", "POST"))
        self.assertEqual(kwargs["body"], {"pin": "012345", "totp": "287082"})
        self.assertEqual(kwargs["header_params"]["x-api-key"], "app")
        self.assertEqual(
            self.client.return_value.rest_client.pool_manager.connection_pool_kw[
                "retries"
            ],
            False,
        )
        self.assertNotIn("012345", repr(self.settings))
        self.client.return_value.pool.join.assert_called_once()

    def test_default_does_not_reconnect_a_disconnected_account(self):
        self.assertEqual(self.service.retrieve_token(), "disconnected")
        self.client.assert_not_called()
        self.save.assert_not_called()

    def test_active_worker_prevents_token_request(self):
        self.lock.return_value.acquire.return_value = False
        with self.assertRaises(self.service.TokenRetrievalError):
            self.service.retrieve_token(connect=True)
        self.client.assert_not_called()
        self.save.assert_not_called()

    def test_live_worker_also_prevents_issuance_and_releases_paper_lock(self):
        self.lock.return_value.acquire.side_effect = [True, False]
        with self.assertRaises(self.service.TokenRetrievalError):
            self.service.retrieve_token(connect=True)
        self.assertEqual(
            [call.args[2] for call in self.lock.call_args_list], ["paper", "live"]
        )
        self.client.assert_not_called()
        self.assertEqual(self.lock.return_value.close.call_count, 2)

    def test_commit_failure_reports_failure_without_retrying_issuance(self):
        self.save.side_effect = RuntimeError("SECRET")
        with self.assertRaises(self.service.TokenRetrievalError) as error:
            self.service.retrieve_token(connect=True, now=59)
        self.assertNotIn("SECRET", str(error.exception))
        self.assertEqual(self.client.return_value.call_api.call_count, 1)

    def test_existing_valid_token_is_not_rotated(self):
        self.get.return_value = SimpleNamespace(access_token="old")
        self.assertEqual(self.service.retrieve_token(), "valid")
        self.client.return_value.call_api.assert_not_called()
        self.save.assert_not_called()

    def test_expired_token_is_rotated_and_new_profile_verified(self):
        self.get.return_value = SimpleNamespace(access_token="old")
        self.profile.return_value.get_profile.side_effect = [
            ApiException(status=401),
            SimpleNamespace(status="success", data=SimpleNamespace(user_id="U1")),
        ]
        self.assertEqual(self.service.retrieve_token(now=59), "saved")
        self.save.assert_called_once()

    def test_profile_network_failure_does_not_rotate(self):
        self.get.return_value = SimpleNamespace(access_token="old")
        self.profile.return_value.get_profile.side_effect = OSError("SECRET")
        with self.assertRaises(self.service.TokenRetrievalError) as error:
            self.service.retrieve_token()
        self.assertNotIn("SECRET", str(error.exception))
        self.client.return_value.call_api.assert_not_called()

    def test_wrong_account_or_malformed_response_never_saved(self):
        for response in (
            {},
            {"user_id": "OTHER", "access_token": "SECRET"},
            {"user_id": "U1", "access_token": " "},
            ["SECRET"],
        ):
            self.client.return_value.call_api.return_value = response
            with self.assertRaises(self.service.TokenRetrievalError):
                self.service.retrieve_token(connect=True)
            self.save.assert_not_called()

    def test_wrong_profile_after_issuance_never_saved(self):
        self.profile.return_value.get_profile.return_value = SimpleNamespace(
            status="success", data=SimpleNamespace(user_id="OTHER")
        )
        with self.assertRaises(self.service.TokenRetrievalError):
            self.service.retrieve_token(connect=True)
        self.save.assert_not_called()

    def test_request_error_not_retried_or_exposed(self):
        self.client.return_value.call_api.side_effect = ApiException(
            status=401, reason="SECRET"
        )
        with self.assertRaises(self.service.TokenRetrievalError) as error:
            self.service.retrieve_token(connect=True)
        self.assertNotIn("SECRET", str(error.exception))
        self.assertEqual(self.client.return_value.call_api.call_count, 1)
        self.save.assert_not_called()

    def test_cli_failure_sanitizes_errors(self):
        from app.cli import upstox_token

        output = io.StringIO()
        with (
            patch.object(
                upstox_token, "retrieve_token", side_effect=RuntimeError("SECRET")
            ),
            redirect_stdout(output),
        ):
            self.assertEqual(upstox_token.main([]), 2)
        self.assertNotIn("SECRET", output.getvalue())


class SecretLoggingTests(unittest.TestCase):
    def test_sdk_response_body_is_redacted_even_at_debug_level(self):
        import logging

        from app.core.logging import install_access_filter

        install_access_filter()
        logger = logging.getLogger("upstox_client.rest")
        output = io.StringIO()
        handler = logging.StreamHandler(output)
        previous = logger.level
        logger.addHandler(handler)
        logger.setLevel(logging.DEBUG)
        try:
            logger.debug("response body: %s", b'{"access_token":"SECRET"}')
        finally:
            logger.removeHandler(handler)
            logger.setLevel(previous)
        self.assertNotIn("SECRET", output.getvalue())


class SDKTransportTests(unittest.TestCase):
    def test_real_sdk_sends_totp_json_and_deserializes_token_and_profile(self):
        from urllib3.response import HTTPResponse

        from app.services import upstox_totp

        settings = upstox_totp.TotpSettings(
            "app", "U1", "012345", "GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ"
        )
        inherited_configuration = upstox_totp.sdk.Configuration()
        inherited_configuration.sandbox = True
        inherited_configuration.host = "https://api-sandbox.upstox.com"
        with (
            patch.object(
                upstox_totp.sdk.Configuration, "_default", inherited_configuration
            ),
            patch.object(upstox_totp, "load_settings", return_value=settings),
            patch.object(upstox_totp, "WorkerLock", spec=WorkerLock) as lock,
            patch.object(upstox_totp, "get_token", return_value=None),
            patch.object(upstox_totp, "save_token") as save,
            patch("upstox_client.api_client.ThreadPool"),
            patch("upstox_client.api_client.rest.RESTClientObject") as rest,
        ):
            lock.return_value.acquire.return_value = True
            rest.return_value.pool_manager.connection_pool_kw = {}
            rest.return_value.POST.return_value = HTTPResponse(
                body=b'{"user_id":"U1","access_token":"SECRET"}', status=200
            )
            rest.return_value.GET.return_value = HTTPResponse(
                body=b'{"status":"success","data":{"user_id":"U1"}}', status=200
            )
            self.assertEqual(upstox_totp.retrieve_token(connect=True, now=59), "saved")
            args, kwargs = rest.return_value.POST.call_args
            self.assertEqual(
                args[0], "https://api.upstox.com/v2/totp-login/authorization/token"
            )
            self.assertEqual(kwargs["body"], {"pin": "012345", "totp": "287082"})
            self.assertEqual(kwargs["headers"]["x-api-key"], "app")
            self.assertNotIn("Authorization", kwargs["headers"])
            self.assertEqual(
                rest.return_value.GET.call_args.kwargs["headers"]["Authorization"],
                "Bearer SECRET",
            )
            save.assert_called_once_with("app", "U1", "SECRET", environment="live")
