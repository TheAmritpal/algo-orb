"""Opt-in daily live-token retrieval through the official SDK transport."""

import base64
import hashlib
import hmac
import os
import re
import struct
import time
from contextlib import ExitStack
from dataclasses import dataclass, field

import upstox_client as sdk
from dotenv import load_dotenv
from upstox_client.rest import ApiException

from app.core import config
from app.core.logging import install_access_filter
from app.repositories.tokens import get_token, save_token
from app.repositories.worker_lock import WorkerLock


class TokenRetrievalError(Exception):
    """Sanitized boundary error; never includes broker responses or credentials."""


@dataclass(frozen=True)
class TotpSettings:
    client_id: str
    user_id: str
    pin: str = field(repr=False)
    secret: str = field(repr=False)


def _decode_secret(secret: str) -> bytes:
    # Accept the Base32 setup key, not a six-digit code or an otpauth URI.
    normalized = "".join(secret.split()).upper().rstrip("=")
    if not re.fullmatch(r"[A-Z2-7]{26,256}", normalized):
        raise ValueError("Configure a Base32 TOTP setup secret, not a six-digit code.")
    try:
        decoded = base64.b32decode(normalized + "=" * (-len(normalized) % 8))
    except ValueError:
        raise ValueError("Invalid TOTP setup secret.") from None
    if len(decoded) < 16:
        raise ValueError("TOTP setup secret is too short.")
    return decoded


def load_settings() -> TotpSettings:
    load_dotenv(config.ENV_FILE, override=False)
    prefix = (
        "UPSTOX_LIVE_"
        if any(
            os.environ.get("UPSTOX_LIVE_" + name) for name in ("API_KEY", "API_SECRET")
        )
        else "UPSTOX_"
    )
    client_id = os.environ.get(prefix + "API_KEY", "")
    user_id = os.environ.get("ORB_BROKER_USER_ID", "")
    for value, maximum in ((client_id, 255), (user_id, 100)):
        if (
            not 1 <= len(value) <= maximum
            or not value.isascii()
            or any(not 32 < ord(c) < 127 for c in value)
        ):
            raise ValueError("Configure the live API key and ORB_BROKER_USER_ID.")
    pin = os.environ.get("UPSTOX_TOTP_PIN", "")
    if not re.fullmatch(r"[0-9]{6}", pin):
        raise ValueError("Configure UPSTOX_TOTP_PIN as six digits.")
    secret = os.environ.get("UPSTOX_TOTP_SECRET", "")
    _decode_secret(secret)
    return TotpSettings(client_id, user_id, pin, secret)


def generate_totp(secret: str, now: float | None = None) -> str:
    """RFC 6238: SHA-1, 30-second interval, six digits (preserving zeros)."""
    counter = int((time.time() if now is None else now) // 30)
    digest = hmac.new(
        _decode_secret(secret), struct.pack(">Q", counter), hashlib.sha1
    ).digest()
    offset = digest[-1] & 15
    number = struct.unpack(">I", digest[offset : offset + 4])[0] & 0x7FFFFFFF
    return f"{number % 1000000:06d}"


def _valid_profile(client, user_id: str) -> bool:
    try:
        response = sdk.UserApi(client).get_profile("2.0", _request_timeout=(3, 5))
    except ApiException as exc:
        if exc.status == 401:
            return False
        raise
    if (
        getattr(response, "status", None) != "success"
        or getattr(getattr(response, "data", None), "user_id", None) != user_id
    ):
        raise TokenRetrievalError("Unable to verify the configured broker account.")
    return True


def retrieve_token(*, connect: bool = False, now: float | None = None) -> str:
    """Renew saved credentials; initial connection requires explicit opt-in.

    Never starts trading. Only a confirmed 401 on an existing token permits
    rotation; temporary broker failures do not cause repeated token issuance.
    """
    client = None
    try:
        install_access_filter()
        settings = load_settings()
        with ExitStack() as stack:
            locks = []
            for mode in ("paper", "live"):
                lock = WorkerLock(settings.client_id, settings.user_id, mode)
                stack.callback(lock.close)
                if not lock.acquire():
                    raise TokenRetrievalError(
                        "Stop strategy workers before retrieving a token."
                    )
                locks.append(lock)
            existing = get_token(
                settings.client_id, settings.user_id, environment="live"
            )
            if existing is None and not connect:
                return "disconnected"
            configuration = sdk.Configuration()
            # This SDK copies a process-wide default and can ignore constructor
            # arguments after a sandbox client has initialized it.
            configuration.sandbox = False
            configuration.host = "https://api.upstox.com"
            configuration.order_host = "https://api-hft.upstox.com"
            configuration.debug = False
            configuration.access_token = existing.access_token if existing else ""
            client = sdk.ApiClient(configuration)
            client.rest_client.pool_manager.connection_pool_kw["retries"] = False
            if existing is not None and _valid_profile(client, settings.user_id):
                return "valid"
            for lock in locks:
                lock.assert_owned()
            response = client.call_api(
                "/v2/totp-login/authorization/token",
                "POST",
                header_params={
                    "Accept": "application/json",
                    "Content-Type": "application/json",
                    "x-api-key": settings.client_id,
                },
                body={"pin": settings.pin, "totp": generate_totp(settings.secret, now)},
                response_type="object",
                auth_settings=[],
                _return_http_data_only=True,
                _request_timeout=(3, 10),
            )
            if (
                not isinstance(response, dict)
                or response.get("user_id") != settings.user_id
            ):
                raise TokenRetrievalError(
                    "Token response did not match the configured account."
                )
            token = response.get("access_token")
            if (
                not isinstance(token, str)
                or not 1 <= len(token) <= 4096
                or any(c.isspace() for c in token)
            ):
                raise TokenRetrievalError(
                    "Upstox returned an incomplete token response."
                )
            client.configuration.access_token = token
            if not _valid_profile(client, settings.user_id):
                raise TokenRetrievalError("Upstox did not accept the new token.")
            for lock in locks:
                lock.assert_owned()
            save_token(settings.client_id, settings.user_id, token, environment="live")
            return "saved"
    except TokenRetrievalError:
        raise
    except Exception:  # noqa: BLE001 -- sanitize SDK/DB errors containing credentials
        # A request may have issued a token even if its response or DB commit
        # failed. No automatic retry, and never expose exceptions with secrets.
        raise TokenRetrievalError(
            "Token retrieval failed. Check configuration, clock, broker and database availability. A prior token may have been revoked; no automatic retry was made."
        ) from None
    finally:
        if client is not None:
            for cleanup in (
                client.rest_client.pool_manager.clear,
                client.pool.close,
                client.pool.join,
            ):
                try:
                    cleanup()
                except (OSError, ValueError, RuntimeError):
                    pass
