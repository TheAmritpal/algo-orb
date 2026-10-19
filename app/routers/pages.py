from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse

from app.core.templating import templates
from app.repositories.sessions import Session
from app.services.auth import require_session
from app.services.sandbox import sandbox_status
from app.services.strategy_history import get_history
from app.services.strategy_status import get_strategy_status
from app.services.upstox import connection_status
from app.strategies.rules import IST

router = APIRouter()


@router.get("/", response_class=HTMLResponse)
def home(request: Request, session: Annotated[Session, Depends(require_session)]):
    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={
            "title": request.app.title,
            "session": session,
            "upstox": connection_status(),
            "strategy": get_strategy_status(),
            "sandbox": sandbox_status(),
        },
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
