"""Upstox authorization-code login and callback; no trading operations."""

import hashlib
import hmac
import logging
import secrets
import time
from urllib.parse import urlencode

import pymysql
import upstox_client
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, RedirectResponse
from upstox_client.rest import ApiException
from urllib3.exceptions import HTTPError

from app.config import get_upstox_config
from app.tokens import save_token

router = APIRouter(prefix="/auth/upstox", tags=["Upstox authentication"])
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


@router.get("/login")
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


@router.get("/callback")
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
    except (ApiException, HTTPError, OSError, ValueError):
        return _error(502, "Upstox token exchange failed. Start Upstox login again.")
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
