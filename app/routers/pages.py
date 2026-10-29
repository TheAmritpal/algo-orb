from __future__ import annotations

from fastapi import APIRouter, Request
from starlette.responses import RedirectResponse

from app.core.errors import AppError
from app.core.templating import templates
from app.routers.deps import CSRF_COOKIE, current_user, issue_csrf_cookie

router = APIRouter()


@router.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/login")
def login_page(request: Request):
    response = templates.TemplateResponse(request, "login.html", {"csrf_token": ""})
    token = issue_csrf_cookie(request, response)
    response.context["csrf_token"] = token
    return response


@router.get("/")
def dashboard(request: Request):
    user = current_user(request)
    if user is None:
        return RedirectResponse("/login", status_code=303)

    service = getattr(request.app.state, "upstox_auth", None)
    try:
        upstox_status = service.status() if service is not None else "unavailable"
    except AppError:
        upstox_status = "unavailable"

    csrf = request.cookies.get(CSRF_COOKIE)
    response = templates.TemplateResponse(
        request,
        "dashboard.html",
        {"user": user, "upstox_status": upstox_status, "csrf_token": csrf or ""},
    )
    if not csrf:
        token = issue_csrf_cookie(request, response)
        response.context["csrf_token"] = token
    return response
