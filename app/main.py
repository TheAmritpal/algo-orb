"""Application composition; run with uvicorn app.main:app."""

from fastapi import FastAPI, Request
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles

from app.core.templating import APP_DIRECTORY
from app.routers import auth, pages, upstox
from app.services.auth import LoginRequired

app = FastAPI(title="Algo ORB", docs_url=None, redoc_url=None, openapi_url=None)


@app.exception_handler(LoginRequired)
async def login_required(request: Request, exc: LoginRequired):
    return RedirectResponse("/login", status_code=303)


@app.middleware("http")
async def private_responses(request: Request, call_next):
    response = await call_next(request)
    if not request.url.path.startswith("/static/"):
        response.headers["Cache-Control"] = "no-store"
        response.headers["Pragma"] = "no-cache"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; style-src 'self'; form-action 'self'; frame-ancestors 'none'; base-uri 'self'"
    )
    return response


app.mount(
    "/static", StaticFiles(directory=str(APP_DIRECTORY / "static")), name="static"
)
app.include_router(auth.router)
app.include_router(pages.router)
app.include_router(upstox.router)
