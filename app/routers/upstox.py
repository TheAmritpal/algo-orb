from typing import Annotated

from fastapi import APIRouter, Depends, Request

from app.repositories.sessions import Session
from app.services import upstox
from app.services.auth import require_session

router = APIRouter(
    prefix="/auth/upstox",
    tags=["Upstox authentication"],
    dependencies=[Depends(require_session)],
)


@router.get("/login")
def login():
    return upstox.login()


@router.get("/callback")
def callback(request: Request, code: str = "", state: str = "", error: str = ""):
    return upstox.callback(request, code, state, error)


@router.post("/sandbox/token")
async def import_sandbox_token(
    request: Request, session: Annotated[Session, Depends(require_session)]
):
    import hmac

    from fastapi import HTTPException
    from fastapi.responses import RedirectResponse
    from starlette.concurrency import run_in_threadpool

    from app.core import config
    from app.repositories import tokens
    from app.routers.auth import bounded_form
    from app.services.auth import BACKEND_ERRORS

    form = await bounded_form(request, {"csrf_token", "access_token"})
    if not hmac.compare_digest(
        form["csrf_token"].encode(), session.csrf_token.encode()
    ):
        raise HTTPException(403, "Invalid form submission")
    token = form["access_token"].strip()
    if not 1 <= len(token) <= 4096 or any(c.isspace() for c in token):
        raise HTTPException(400, "Invalid sandbox token")
    try:
        settings = config.get_sandbox_config()
        await run_in_threadpool(
            tokens.save_token,
            settings["client_id"],
            settings["user_id"],
            token,
            environment="sandbox",
        )
    except BACKEND_ERRORS:
        raise HTTPException(
            503, "Unable to save sandbox token. Check configuration and migrations."
        ) from None
    return RedirectResponse("/", status_code=303, headers=upstox.PRIVATE_HEADERS)


@router.get("/sandbox/login")
def sandbox_login():
    from fastapi.responses import RedirectResponse

    return RedirectResponse(
        "https://account.upstox.com/developer/apps#sandbox",
        status_code=303,
        headers=upstox.PRIVATE_HEADERS,
    )


@router.post("/disconnect")
async def disconnect(
    request: Request, session: Annotated[Session, Depends(require_session)]
):
    import hmac

    from fastapi import HTTPException
    from fastapi.responses import RedirectResponse
    from starlette.concurrency import run_in_threadpool

    from app.routers.auth import bounded_form
    from app.services import upstox_disconnect

    form = await bounded_form(request, {"csrf_token", "user_id"})
    if not hmac.compare_digest(
        form["csrf_token"].encode(), session.csrf_token.encode()
    ):
        raise HTTPException(403, "Invalid form submission")
    user_id = form["user_id"]
    if (
        not 1 <= len(user_id) <= 100
        or not user_id.isascii()
        or any(c.isspace() for c in user_id)
    ):
        raise HTTPException(400, "Invalid Upstox account")
    await run_in_threadpool(upstox_disconnect.disconnect, user_id)
    response = RedirectResponse("/", status_code=303, headers=upstox.PRIVATE_HEADERS)
    response.delete_cookie(upstox.COOKIE, path=upstox.COOKIE_PATH)
    return response
