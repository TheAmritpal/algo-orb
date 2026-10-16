from typing import Annotated

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse

from app.core.templating import templates
from app.repositories.sessions import Session
from app.services.auth import require_session
from app.services.upstox import connection_status
from app.services.strategy_status import get_strategy_status

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
        },
    )


@router.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
