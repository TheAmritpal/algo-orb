"""Browser login/logout routes with bounded forms and CSRF protection."""

import hmac
import re
from typing import Annotated
from urllib.parse import parse_qsl

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from starlette.concurrency import run_in_threadpool

from app.core.config import get_auth_settings
from app.core.security import hash_token, new_login_csrf, valid_login_csrf
from app.core.templating import templates
from app.repositories import sessions
from app.repositories.sessions import Session
from app.services import auth

router = APIRouter()


def settings():
    try:
        return get_auth_settings()
    except ValueError:
        raise auth.unavailable() from None


def login_page(request: Request, error: str = "", status_code: int = 200):
    config = settings()
    csrf = new_login_csrf(config.secret)
    response = templates.TemplateResponse(
        request=request,
        name="login.html",
        context={"title": "Sign in · Algo ORB", "csrf_token": csrf, "error": error},
        status_code=status_code,
    )
    response.set_cookie(
        auth.LOGIN_CSRF_COOKIE,
        csrf,
        max_age=600,
        httponly=True,
        secure=config.cookie_secure,
        samesite="lax",
        path="/",
    )
    return response


async def bounded_form(
    request: Request, expected: set[str], *, optional: frozenset[str] = frozenset()
) -> dict[str, str]:
    if (
        request.headers.get("content-type", "").split(";", 1)[0].lower()
        != "application/x-www-form-urlencoded"
    ):
        raise HTTPException(400, "Invalid form submission")
    body = bytearray()
    async for chunk in request.stream():
        if len(body) + len(chunk) > 8192:
            raise HTTPException(413, "Form is too large")
        body.extend(chunk)
    try:
        decoded = body.decode("utf-8", errors="strict")
        if re.search(r"%(?![0-9a-fA-F]{2})", decoded):
            raise ValueError("Invalid percent encoding")
        pairs = parse_qsl(
            decoded,
            keep_blank_values=True,
            strict_parsing=True,
            encoding="utf-8",
            errors="strict",
            max_num_fields=8,
        )
        form = dict(pairs)
        if (
            len(form) != len(pairs)
            or not expected <= set(form)
            or set(form) - expected - optional
        ):
            raise ValueError("Invalid fields")
        return form
    except (ValueError, UnicodeError):
        raise HTTPException(400, "Invalid form submission") from None


@router.get("/login")
def login(request: Request):
    return login_page(request)


@router.post("/login")
async def submit_login(request: Request):
    config = settings()
    form = await bounded_form(request, {"username", "password", "csrf_token"})
    username, password = form["username"], form["password"]
    if not 1 <= len(username) <= 128 or not 1 <= len(password) <= 1024:
        raise HTTPException(400, "Invalid form submission")
    if not valid_login_csrf(
        form["csrf_token"],
        request.cookies.get(auth.LOGIN_CSRF_COOKIE, ""),
        config.secret,
    ):
        raise HTTPException(403, "Invalid or expired form. Reload the login page.")
    client = request.client.host if request.client else "unknown"
    if not auth.limiter.reserve(client):
        raise HTTPException(
            429,
            "Too many login attempts. Try again later.",
            headers={"Retry-After": "300"},
        )
    try:
        user = await run_in_threadpool(auth.authenticate, username, password)
        if user is None:
            return login_page(request, "Invalid username or password", 401)
        token, _ = await run_in_threadpool(auth.issue_session, user)
    except sessions.AuthenticationChanged:
        return login_page(request, "Invalid username or password", 401)
    except auth.BACKEND_ERRORS:
        raise auth.unavailable() from None
    auth.limiter.succeeded(client)
    response = RedirectResponse("/", status_code=303)
    response.set_cookie(
        auth.SESSION_COOKIE,
        token,
        max_age=auth.SESSION_TTL,
        httponly=True,
        secure=config.cookie_secure,
        samesite="lax",
        path="/",
    )
    response.delete_cookie(
        auth.LOGIN_CSRF_COOKIE,
        path="/",
        secure=config.cookie_secure,
        httponly=True,
        samesite="lax",
    )
    return response


@router.post("/logout")
async def logout(
    request: Request, session: Annotated[Session, Depends(auth.require_session)]
):
    form = await bounded_form(request, {"csrf_token"})
    if not hmac.compare_digest(
        form["csrf_token"].encode(), session.csrf_token.encode()
    ):
        raise HTTPException(403, "Invalid form submission")
    try:
        await run_in_threadpool(
            sessions.delete_session, hash_token(request.cookies[auth.SESSION_COOKIE])
        )
    except auth.BACKEND_ERRORS:
        raise auth.unavailable() from None
    response = RedirectResponse("/login", status_code=303)
    response.delete_cookie(
        auth.SESSION_COOKIE,
        path="/",
        secure=settings().cookie_secure,
        httponly=True,
        samesite="lax",
    )
    return response
