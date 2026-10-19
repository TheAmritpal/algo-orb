import hmac
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from starlette.concurrency import run_in_threadpool

from app.core.templating import templates
from app.repositories.sessions import Session
from app.repositories.trading_config import (
    TradingConfig,
    get_trading_config,
    save_trading_config,
)
from app.routers.auth import bounded_form
from app.services.auth import BACKEND_ERRORS, require_session
from app.services.sandbox import sandbox_status
from app.services.strategy_history import get_history
from app.services.strategy_status import get_strategy_status
from app.services.upstox import connection_status
from app.strategies.rules import IST

router = APIRouter()


@router.get("/", response_class=HTMLResponse)
def home(request: Request, session: Annotated[Session, Depends(require_session)]):
    try:
        trading_config = get_trading_config()
    except BACKEND_ERRORS:
        trading_config = None
    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={
            "title": request.app.title,
            "session": session,
            "upstox": connection_status(),
            "strategy": get_strategy_status(),
            "sandbox": sandbox_status(),
            "trading_config": trading_config,
            "config_saved": request.query_params.get("config_saved") == "1",
        },
        headers={"Cache-Control": "no-store"},
    )


@router.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/strategy/history", response_class=HTMLResponse)
def strategy_history(
    request: Request,
    session: Annotated[Session, Depends(require_session)],
    mode: Literal["paper", "live"] | None = None,
    run_id: int | None = Query(None, ge=1),
    page: int = Query(1, ge=1, le=10000),
):
    return templates.TemplateResponse(
        request=request,
        name="strategy_history.html",
        context={
            "title": "Strategy history",
            "session": session,
            "ist": IST,
            "history": get_history(mode=mode, run_id=run_id, page=page),
        },
        headers={"Cache-Control": "no-store"},
    )


@router.post("/strategy/config")
async def update_trading_config(
    request: Request, session: Annotated[Session, Depends(require_session)]
):
    form = await bounded_form(
        request, {"csrf_token", "mode"}, optional=frozenset({"live_enabled"})
    )
    form.setdefault("live_enabled", "false")
    if not hmac.compare_digest(
        form["csrf_token"].encode(), session.csrf_token.encode()
    ):
        raise HTTPException(403, "Invalid form submission")
    if (
        form["mode"] not in {"paper", "live"}
        or form["live_enabled"] not in {"true", "false"}
        or (form["mode"] == "live" and form["live_enabled"] != "true")
    ):
        raise HTTPException(
            400, "Select a valid mode and explicitly enable live trading for Live mode."
        )
    config = TradingConfig(form["mode"], form["live_enabled"] == "true")
    try:
        await run_in_threadpool(save_trading_config, config)
    except BACKEND_ERRORS:
        raise HTTPException(
            503, "Trading settings unavailable. Check database access and migrations."
        ) from None
    return RedirectResponse("/?config_saved=1", status_code=303)
