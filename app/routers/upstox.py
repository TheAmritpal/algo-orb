from __future__ import annotations

import hmac

from fastapi import APIRouter, Request
from starlette.responses import JSONResponse, RedirectResponse

from app.core.errors import AppError, UpstoxOAuthError
from app.routers.deps import UPSTOX_STATE_COOKIE, current_user

router = APIRouter()


@router.get("/auth/upstox/login")
def upstox_login(request: Request):
    if current_user(request) is None:
        return RedirectResponse("/login", status_code=303)
    try:
        url, state = request.app.state.upstox_auth.login_url()
    except UpstoxOAuthError as exc:
        return RedirectResponse(f"/?upstox=error&message={exc.public_message}", status_code=303)
    response = RedirectResponse(url, status_code=303)
    response.set_cookie(
        UPSTOX_STATE_COOKIE,
        state,
        httponly=True,
        samesite="lax",
        secure=request.app.state.settings.app_cookie_secure,
        max_age=600,
        path="/",
    )
    return response


@router.get("/auth/upstox/callback")
def upstox_callback(request: Request, code: str | None = None, state: str | None = None):
    if current_user(request) is None:
        return RedirectResponse("/login", status_code=303)
    cookie_state = request.cookies.get(UPSTOX_STATE_COOKIE)
    if not cookie_state or not state or not hmac.compare_digest(cookie_state, state):
        return JSONResponse({"error": "Invalid OAuth state"}, status_code=400)
    try:
        request.app.state.upstox_auth.complete(code or "", state)
    except AppError:
        response = RedirectResponse("/?upstox=error", status_code=303)
    else:
        response = RedirectResponse("/?upstox=connected", status_code=303)
    response.delete_cookie(UPSTOX_STATE_COOKIE, path="/")
    return response


@router.get("/auth/upstox/status")
def upstox_status(request: Request):
    if current_user(request) is None:
        return JSONResponse({"error": "Authentication required"}, status_code=401)
    return JSONResponse({"status": request.app.state.upstox_auth.status()})
