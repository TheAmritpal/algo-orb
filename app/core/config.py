"""Read local configuration without opening external connections."""

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

ENV_FILE = Path(__file__).resolve().parents[2] / ".env"


def get_upstox_config() -> dict[str, str]:
    load_dotenv(ENV_FILE, override=False)
    prefix = (
        "UPSTOX_LIVE_"
        if any(
            os.environ.get("UPSTOX_LIVE_" + key) for key in ("API_KEY", "API_SECRET")
        )
        else "UPSTOX_"
    )
    required = (prefix + "API_KEY", prefix + "API_SECRET")
    missing = [name for name in required if not os.environ.get(name)]
    if missing:
        raise ValueError(f"Missing Upstox configuration: {', '.join(missing)}")
    return {
        "client_id": os.environ[prefix + "API_KEY"],
        "client_secret": os.environ[prefix + "API_SECRET"],
        "redirect_uri": os.environ.get(prefix + "REDIRECT_URI")
        or os.environ.get("UPSTOX_REDIRECT_URI")
        or "http://localhost:8000/auth/upstox/callback",
    }


def get_sandbox_config() -> dict[str, str]:
    load_dotenv(ENV_FILE, override=False)
    client_id = os.environ.get("UPSTOX_SANDBOX_API_KEY", "").strip()
    user_id = os.environ.get("UPSTOX_SANDBOX_USER_ID", "sandbox").strip()
    if not client_id or not user_id or len(client_id) > 255 or len(user_id) > 100:
        raise ValueError("Configure sandbox app and account identifier")
    if not client_id.isascii() or not user_id.isascii():
        raise ValueError("Sandbox identifiers must be ASCII")
    return {"client_id": client_id, "user_id": user_id}


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


@dataclass(frozen=True)
class AuthSettings:
    secret: str
    cookie_secure: bool


def get_auth_settings() -> AuthSettings:
    load_dotenv(ENV_FILE, override=False)
    secret = os.environ.get("APP_SESSION_SECRET", "")
    if len(secret) < 32 or secret.startswith("replace_"):
        raise ValueError(
            "APP_SESSION_SECRET must contain at least 32 random characters"
        )
    secure = os.environ.get("APP_COOKIE_SECURE", "true").lower()
    if secure not in {"true", "false"}:
        raise ValueError("APP_COOKIE_SECURE must be true or false")
    return AuthSettings(secret=secret, cookie_secure=secure == "true")


def get_admin_credentials() -> tuple[str, str]:
    load_dotenv(ENV_FILE, override=False)
    username = os.environ.get("APP_USERNAME", "")
    password = os.environ.get("APP_PASSWORD", "")
    if not username.strip() or username != username.strip() or len(username) > 128:
        raise ValueError(
            "APP_USERNAME must contain 1 to 128 characters without surrounding spaces"
        )
    if (
        not password.strip()
        or len(password) > 1024
        or password.lower() in {"password", "changeme", "admin"}
        or password.startswith("replace_")
    ):
        raise ValueError(
            "APP_PASSWORD must be configured with a non-placeholder password"
        )
    return username, password
