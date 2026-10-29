from __future__ import annotations

import time

from fastapi import APIRouter, Request
from starlette.responses import RedirectResponse

from app.core.errors import AppError
from app.core.templating import templates
from app.routers.deps import (
    current_user,
    issue_csrf_token,
    set_csrf_cookie,
    valid_csrf_cookie,
)

router = APIRouter()
STATUS_CACHE_SECONDS = 60


@router.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/login")
def login_page(request: Request):
    token = issue_csrf_token(request)
    response = templates.TemplateResponse(
        request, "login.html", {"csrf_token": token}
    )
    set_csrf_cookie(request, response, token)
    return response


def _upstox_status(request: Request) -> str:
    service = getattr(request.app.state, "upstox_auth", None)
    if service is None:
        return "unavailable"
    now = time.time()
    cached = getattr(request.app.state, "upstox_status_cache", None)
    if cached is not None and now - cached[0] < STATUS_CACHE_SECONDS:
        return cached[1]
    try:
        status = service.status()
    except AppError:
        status = "unavailable"
    request.app.state.upstox_status_cache = (now, status)
    return status


@router.get("/")
def dashboard(request: Request):
    user = current_user(request)
    if user is None:
        return RedirectResponse("/login", status_code=303)

    csrf = valid_csrf_cookie(request)
    needs_cookie = csrf is None
    if csrf is None:
        csrf = issue_csrf_token(request)

    response = templates.TemplateResponse(
        request,
        "dashboard.html",
        {
            "user": user,
            "upstox_status": _upstox_status(request),
            "csrf_token": csrf,
            "trading_mode": request.app.state.settings.trading_mode,
        },
    )
    if needs_cookie:
        set_csrf_cookie(request, response, csrf)
    return response
