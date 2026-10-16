from fastapi import APIRouter, Depends, Request

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
