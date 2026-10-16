from pathlib import Path

from fastapi.templating import Jinja2Templates

APP_DIRECTORY = Path(__file__).resolve().parents[1]
templates = Jinja2Templates(directory=str(APP_DIRECTORY / "templates"))
