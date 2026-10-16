"""Read local configuration without opening external connections."""

import os
from pathlib import Path

from dotenv import load_dotenv

ENV_FILE = Path(__file__).resolve().parent.parent / ".env"


def get_upstox_config() -> dict[str, str]:
    load_dotenv(ENV_FILE, override=False)
    required = ("UPSTOX_API_KEY", "UPSTOX_API_SECRET")
    missing = [name for name in required if not os.environ.get(name)]
    if missing:
        raise ValueError(f"Missing Upstox configuration: {', '.join(missing)}")
    return {
        "client_id": os.environ["UPSTOX_API_KEY"],
        "client_secret": os.environ["UPSTOX_API_SECRET"],
        "redirect_uri": os.environ.get("UPSTOX_REDIRECT_URI")
        or "http://localhost:8000/auth/upstox/callback",
    }


def get_mysql_config() -> dict[str, str | int]:
    load_dotenv(ENV_FILE, override=False)
    required = ("MYSQL_HOST", "MYSQL_USER", "MYSQL_PASSWORD", "MYSQL_DATABASE")
    missing = [name for name in required if not os.environ.get(name)]
    if missing:
        raise ValueError(f"Missing MySQL configuration: {', '.join(missing)}")
    try:
        port = int(os.environ.get("MYSQL_PORT", "3306"))
    except ValueError:
        raise ValueError("MYSQL_PORT must be an integer between 1 and 65535") from None
    if not 1 <= port <= 65535:
        raise ValueError("MYSQL_PORT must be an integer between 1 and 65535")
    return {
        "host": os.environ["MYSQL_HOST"],
        "port": port,
        "user": os.environ["MYSQL_USER"],
        "password": os.environ["MYSQL_PASSWORD"],
        "database": os.environ["MYSQL_DATABASE"],
    }
