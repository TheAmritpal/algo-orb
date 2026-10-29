from __future__ import annotations

import hmac

from fastapi import HTTPException, Request
from starlette.responses import Response

from app.core.security import issue_csrf, verify_csrf
from app.services.auth import AuthenticatedUser

SESSION_COOKIE = "algo_session"
CSRF_COOKIE = "algo_csrf"
UPSTOX_STATE_COOKIE = "algo_upstox_state"


def current_user(request: Request) -> AuthenticatedUser | None:
    token = request.cookies.get(SESSION_COOKIE)
    return request.app.state.auth.authenticate(token)


def require_csrf(request: Request, form_token: str | None) -> None:
    cookie = request.cookies.get(CSRF_COOKIE)
    secret = request.app.state.settings.app_session_secret
    if (
        not cookie
        or not form_token
        or not hmac.compare_digest(cookie, form_token)
        or not verify_csrf(secret, cookie)
    ):
        raise HTTPException(status_code=403, detail="Invalid CSRF token")


def issue_csrf_token(request: Request) -> str:
    return issue_csrf(request.app.state.settings.app_session_secret)


def set_csrf_cookie(request: Request, response: Response, token: str) -> None:
    response.set_cookie(
        CSRF_COOKIE,
        token,
        httponly=True,
        samesite="lax",
        secure=request.app.state.settings.app_cookie_secure,
        max_age=request.app.state.settings.app_session_ttl_seconds,
        path="/",
    )


def valid_csrf_cookie(request: Request) -> str | None:
    cookie = request.cookies.get(CSRF_COOKIE)
    if not cookie:
        return None
    secret = request.app.state.settings.app_session_secret
    return cookie if verify_csrf(secret, cookie) else None


def issue_csrf_cookie(request: Request, response: Response, max_age: int | None = None) -> str:
    token = issue_csrf_token(request)
    response.set_cookie(
        CSRF_COOKIE,
        token,
        httponly=True,
        samesite="lax",
        secure=request.app.state.settings.app_cookie_secure,
        max_age=max_age or request.app.state.settings.app_session_ttl_seconds,
        path="/",
    )
    return token


def set_session_cookie(request: Request, response: Response, token: str) -> None:
    settings = request.app.state.settings
    response.set_cookie(
        SESSION_COOKIE,
        token,
        httponly=True,
        samesite="lax",
        secure=settings.app_cookie_secure,
        max_age=settings.app_session_ttl_seconds,
        path="/",
    )


def clear_session_cookie(request: Request, response: Response) -> None:
    response.delete_cookie(
        SESSION_COOKIE,
        path="/",
        secure=request.app.state.settings.app_cookie_secure,
        httponly=True,
        samesite="lax",
    )
