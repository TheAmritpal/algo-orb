from __future__ import annotations

from pathlib import Path

import pymysql
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

from app.core.config import Settings, get_settings
from app.core.errors import AppError
from app.core.logging import configure_logging
from app.repositories.sessions import SessionRepository
from app.repositories.users import UserRepository
from app.routers import auth, pages
from app.services.auth import AuthService


def create_app(
    settings: Settings | None = None,
    *,
    users=None,
    sessions=None,
    tokens=None,
    audit=None,
    broker=None,
    sdk=None,
) -> FastAPI:
    resolved = settings or get_settings()
    configure_logging()

    app = FastAPI(
        title="Algo ORB",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    app.state.settings = resolved
    app.state.users = users or UserRepository(resolved)
    app.state.sessions = sessions or SessionRepository(resolved)
    app.state.tokens = tokens
    app.state.audit = audit
    app.state.broker = broker
    app.state.sdk = sdk
    app.state.auth = AuthService(
        users=app.state.users,
        sessions=app.state.sessions,
        session_ttl_seconds=resolved.app_session_ttl_seconds,
    )

    static_dir = Path(__file__).resolve().parent / "static"
    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

    app.include_router(pages.router)
    app.include_router(auth.router)

    @app.exception_handler(AppError)
    async def app_error_handler(request: Request, exc: AppError) -> HTMLResponse:
        return HTMLResponse(
            f"<h1>{exc.public_message}</h1>",
            status_code=exc.status_code,
        )

    @app.exception_handler(pymysql.MySQLError)
    async def database_error_handler(
        request: Request, exc: pymysql.MySQLError
    ) -> HTMLResponse:
        return HTMLResponse(
            "<h1>Database unavailable. Try again shortly.</h1>",
            status_code=503,
        )

    return app


app = create_app()
