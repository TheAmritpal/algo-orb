"""Upstox authorization-code login and callback; no trading operations."""

import hashlib
import hmac
import json
import logging
import re
import secrets
import time
from dataclasses import dataclass
from typing import Literal
from urllib.parse import urlencode

import pymysql
import upstox_client
from fastapi import Request
from fastapi.responses import JSONResponse, RedirectResponse
from upstox_client.rest import ApiException
from urllib3.exceptions import HTTPError

from app.core.config import get_upstox_config
from app.repositories.tokens import get_latest_token, save_token

logger = logging.getLogger(__name__)
COOKIE = "upstox_oauth_state"
COOKIE_PATH = "/auth/upstox"
STATE_TTL = 600
PRIVATE_HEADERS = {
    "Cache-Control": "no-store",
    "Pragma": "no-cache",
    "Referrer-Policy": "no-referrer",
}


def _new_state(secret: str) -> str:
    payload = f"{int(time.time())}.{secrets.token_urlsafe(32)}"
    signature = hmac.new(secret.encode(), payload.encode(), hashlib.sha256).hexdigest()
    return f"{payload}.{signature}"


def _valid_state(state: str, cookie: str, secret: str) -> bool:
    if (
        not state
        or len(state) > 1024
        or not hmac.compare_digest(state.encode(), cookie.encode())
    ):
        return False
    try:
        timestamp, nonce, signature = state.split(".")
        age = time.time() - int(timestamp)
    except ValueError:
        return False
    payload = f"{timestamp}.{nonce}"
    expected = hmac.new(secret.encode(), payload.encode(), hashlib.sha256).hexdigest()
    return 0 <= age <= STATE_TTL and hmac.compare_digest(
        signature.encode(), expected.encode()
    )


def _error(status: int, detail: str) -> JSONResponse:
    response = JSONResponse(
        {"detail": detail}, status_code=status, headers=PRIVATE_HEADERS
    )
    response.delete_cookie(COOKIE, path=COOKIE_PATH)
    return response


def _storage_error(exc: Exception) -> JSONResponse:
    # Never log exception text or tracebacks: MySQL errors can contain SQL values.
    code = (
        exc.args[0]
        if isinstance(exc, pymysql.MySQLError)
        and exc.args
        and isinstance(exc.args[0], int)
        else None
    )
    logger.error(
        "Upstox token storage failed: error_type=%s mysql_error=%s",
        type(exc).__name__,
        code,
    )
    instructions = {
        1045: "MySQL rejected authentication. Check MYSQL_USER and MYSQL_PASSWORD in .env.",
        1049: "The configured database does not exist. Create MYSQL_DATABASE and run uv run alembic upgrade head.",
        1146: "The token table is missing. Run uv run alembic upgrade head before logging in.",
        1142: "The MySQL user needs INSERT and UPDATE privileges on upstox_tokens.",
        2002: "Cannot connect to MySQL. Check the server, MYSQL_HOST, and MYSQL_PORT.",
        2003: "Cannot connect to MySQL. Check the server, MYSQL_HOST, and MYSQL_PORT.",
    }
    detail = instructions.get(
        code, "Unable to save the access token. Check MySQL and start login again."
    )
    if isinstance(exc, RuntimeError) and "cryptography" in str(exc):
        detail = "MySQL authentication requires an optional dependency. Run uv add 'pymysql[rsa]' and restart the app."
    elif isinstance(exc, ValueError):
        detail = (
            "MySQL configuration is missing or invalid. Check MYSQL_* settings in .env."
        )
    return _error(503, detail)


def _token_exchange_error(exc: Exception) -> JSONResponse:
    # Only numeric Upstox codes and local messages may leave this boundary. Broker bodies,
    # reasons, headers and exception text can contain credentials or auth codes.
    instructions = {
        "UDAPI100058": "Your Upstox account has no active trading segments. Reactivate the segments through the Upstox app or website, then start a fresh Upstox login. Contact Upstox support if reactivation is unavailable.",
        "UDAPI100016": "Upstox rejected the credentials. Verify UPSTOX_API_KEY and UPSTOX_API_SECRET belong to the same app, restart the app, and start Upstox login again.",
        "UDAPI100073": "Your Upstox API client is inactive. Check the app in the Upstox developer console or contact Upstox support before starting login again.",
        "UDAPI100069": "Upstox rejected the API credentials. Check UPSTOX_API_KEY and UPSTOX_API_SECRET in .env, restart the app, and start Upstox login again.",
        "UDAPI100070": "Upstox rejected the redirect URI. Set UPSTOX_REDIRECT_URI to exactly the redirect URI registered for your Upstox app, restart the app, and start Upstox login again.",
        "UDAPI100057": "Upstox rejected the authorization code. It may be expired or already used. Start Upstox login again; do not refresh the callback page.",
    }
    broker_code = None
    status = None
    if isinstance(exc, ApiException):
        status = exc.status if type(exc.status) is int else None
        body = exc.body
        if isinstance(body, (str, bytes)) and len(body) <= 65536:
            try:
                payload = json.loads(body)
            except (ValueError, UnicodeError, RecursionError):
                payload = None
            errors = payload.get("errors") if isinstance(payload, dict) else None
            if isinstance(errors, list):
                for error in errors:
                    if not isinstance(error, dict):
                        continue
                    code = error.get("errorCode", error.get("error_code"))
                    if isinstance(code, str) and re.fullmatch(r"UDAPI[0-9]{1,12}", code):
                        broker_code = code
                        if code in instructions:
                            break
    logger.error(
        "Upstox token exchange failed: error_type=%s http_status=%s broker_code=%s",
        type(exc).__name__,
        status,
        broker_code,
    )
    if broker_code in instructions:
        detail = instructions[broker_code]
    elif status == 401:
        detail = "Upstox rejected the token exchange (HTTP 401). Verify UPSTOX_API_KEY and UPSTOX_API_SECRET belong to the same active Upstox app, restart the app, and start a fresh login. The authorization code must be new and unused."
    elif isinstance(exc, (HTTPError, OSError)):
        detail = "Upstox token exchange failed due to a network or TLS error. Check server connectivity and start Upstox login again."
    else:
        detail = "Upstox token exchange failed. Check the server log for the error type and HTTP status, then start Upstox login again."
    if broker_code is not None:
        detail += f" (Upstox {broker_code})"
    return _error(502, detail)


def login():
    try:
        settings = get_upstox_config()
    except ValueError:
        return _error(503, "Configure the Upstox API key and secret in .env.")
    state = _new_state(settings["client_secret"])
    query = urlencode(
        {
            "response_type": "code",
            "client_id": settings["client_id"],
            "redirect_uri": settings["redirect_uri"],
            "state": state,
        }
    )
    response = RedirectResponse(
        f"https://api.upstox.com/v2/login/authorization/dialog?{query}",
        status_code=303,
        headers=PRIVATE_HEADERS,
    )
    response.set_cookie(
        COOKIE,
        state,
        max_age=STATE_TTL,
        httponly=True,
        samesite="lax",
        secure=settings["redirect_uri"].startswith("https://"),
        path=COOKIE_PATH,
    )
    return response


def callback(request: Request, code: str = "", state: str = "", error: str = ""):
    try:
        settings = get_upstox_config()
    except ValueError:
        return _error(503, "Configure the Upstox API key and secret in .env.")
    if not _valid_state(
        state, request.cookies.get(COOKIE, ""), settings["client_secret"]
    ):
        return _error(400, "Invalid or expired login state. Start Upstox login again.")
    if error or not code.strip():
        return _error(
            400, "Upstox login was denied or did not supply an authorization code."
        )

    api = upstox_client.LoginApi()
    try:
        result = api.token(
            "2.0",
            code=code,
            **settings,
            grant_type="authorization_code",
            _request_timeout=15,
        )
    except (ApiException, HTTPError, OSError, ValueError) as exc:
        return _token_exchange_error(exc)
    finally:
        api.api_client.rest_client.pool_manager.clear()

    token = getattr(result, "access_token", None)
    user_id = getattr(result, "user_id", None)
    if (
        not isinstance(token, str)
        or not token.strip()
        or not isinstance(user_id, str)
        or not user_id.strip()
    ):
        return _error(
            502, "Upstox returned an incomplete token response. Start login again."
        )
    try:
        save_token(settings["client_id"], user_id, token)
    except (pymysql.MySQLError, ValueError, RuntimeError, OSError) as exc:
        return _storage_error(exc)
    response = RedirectResponse(url="/", status_code=303, headers=PRIVATE_HEADERS)
    response.delete_cookie(COOKIE, path=COOKIE_PATH)
    return response


@dataclass(frozen=True)
class ConnectionStatus:
    state: Literal["connected", "disconnected", "unavailable"]
    user_id: str | None = None
    message: str = ""


def connection_status() -> ConnectionStatus:
    """Validate persisted credentials with a read-only, bounded profile request."""
    client = None
    try:
        settings = get_upstox_config()
        token = get_latest_token(settings["client_id"])
        if token is None or not token.access_token.strip():
            return ConnectionStatus(
                "disconnected", message="Connect your Upstox account to get started."
            )
        configuration = upstox_client.Configuration()
        configuration.access_token = token.access_token
        client = upstox_client.ApiClient(configuration)
        profile = upstox_client.UserApi(client).get_profile(
            "2.0", _request_timeout=(3, 5)
        )
        data = getattr(profile, "data", None)
        if (
            getattr(profile, "status", None) != "success"
            or getattr(data, "user_id", None) != token.user_id
        ):
            return ConnectionStatus(
                "unavailable",
                message="Unable to confirm your Upstox connection. Please try again shortly.",
            )
        return ConnectionStatus(
            "connected",
            user_id=token.user_id,
            message="Your Upstox account is connected.",
        )
    except ApiException as exc:
        if exc.status == 401:
            return ConnectionStatus(
                "disconnected",
                message="Your Upstox session has expired or was revoked. Connect again to continue.",
            )
        return ConnectionStatus(
            "unavailable",
            message="Unable to check Upstox right now. Please try again shortly.",
        )
    except (pymysql.MySQLError, HTTPError, OSError, ValueError, RuntimeError):
        # Database and broker exceptions may contain credentials or SQL values.
        return ConnectionStatus(
            "unavailable",
            message="Unable to check Upstox right now. Please try again shortly.",
        )
    finally:
        if client is not None:
            # This SDK creates a ThreadPool even for synchronous calls.
            for cleanup in (
                client.rest_client.pool_manager.clear,
                client.pool.close,
                client.pool.join,
            ):
                try:
                    cleanup()
                except (OSError, ValueError, RuntimeError):
                    pass
