from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from app.upstox_auth import router as upstox_auth_router

app = FastAPI(title="Algo ORB", docs_url=None, redoc_url=None, openapi_url=None)
app.include_router(upstox_auth_router)
templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))


@app.get("/", response_class=HTMLResponse)
def home(request: Request):
    return templates.TemplateResponse(
        request=request, name="index.html", context={"title": app.title}
    )


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
