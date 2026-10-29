from __future__ import annotations

from fastapi import APIRouter, Form, Request
from starlette.responses import RedirectResponse

from app.core.errors import AuthError
from app.core.templating import templates
from app.routers.deps import (
    SESSION_COOKIE,
    clear_session_cookie,
    require_csrf,
    set_session_cookie,
)

router = APIRouter()


@router.post("/login")
def login(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    csrf_token: str | None = Form(None),
):
    require_csrf(request, csrf_token)
    client_key = request.client.host if request.client else "unknown"
    try:
        token = request.app.state.auth.login(username, password, client_key=client_key)
    except AuthError as exc:
        return templates.TemplateResponse(
            request,
            "login.html",
            {"csrf_token": csrf_token, "error": exc.public_message},
            status_code=exc.status_code,
        )
    response = RedirectResponse("/", status_code=303)
    set_session_cookie(request, response, token)
    return response


@router.post("/logout")
def logout(request: Request, csrf_token: str | None = Form(None)):
    require_csrf(request, csrf_token)
    request.app.state.auth.logout(request.cookies.get(SESSION_COOKIE))
    response = RedirectResponse("/login", status_code=303)
    clear_session_cookie(request, response)
    return response
