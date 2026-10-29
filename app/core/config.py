from __future__ import annotations

from decimal import Decimal
from functools import lru_cache
from typing import Literal

from pydantic import Field
from pydantic import ValidationError as PydanticValidationError
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.core.errors import ConfigError


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    app_env: Literal["development", "production"] = "development"
    app_session_secret: str = Field(min_length=16)
    app_cookie_secure: bool = False
    app_username: str
    app_password: str
    app_session_ttl_seconds: int = 43_200
    login_max_attempts: int = 5
    login_window_seconds: int = 300

    mysql_host: str
    mysql_port: int = 3306
    mysql_user: str
    mysql_password: str
    mysql_database: str

    upstox_environment: Literal["sandbox", "live"] = "sandbox"
    upstox_sandbox_api_key: str = ""
    upstox_sandbox_api_secret: str = ""
    upstox_sandbox_redirect_uri: str = ""
    upstox_live_api_key: str = ""
    upstox_live_api_secret: str = ""
    upstox_live_redirect_uri: str = ""
    upstox_account_user_id: str = ""

    trading_mode: Literal["paper", "live"] = "paper"
    trading_enabled: bool = False
    order_max_notional: Decimal = Field(gt=0)
    order_max_quantity: int = Field(gt=0)

    @property
    def upstox_api_key(self) -> str:
        if self.upstox_environment == "live":
            return self.upstox_live_api_key
        return self.upstox_sandbox_api_key

    @property
    def upstox_api_secret(self) -> str:
        if self.upstox_environment == "live":
            return self.upstox_live_api_secret
        return self.upstox_sandbox_api_secret

    @property
    def upstox_redirect_uri(self) -> str:
        if self.upstox_environment == "live":
            return self.upstox_live_redirect_uri
        return self.upstox_sandbox_redirect_uri

    @property
    def is_live(self) -> bool:
        return self.upstox_environment == "live"


def load_settings(_env_file: str | None = ".env") -> Settings:
    try:
        return Settings(_env_file=_env_file)
    except PydanticValidationError as exc:
        names = sorted({str(error["loc"][0]) for error in exc.errors() if error["loc"]})
        raise ConfigError(f"Invalid configuration: {', '.join(names)}") from None


@lru_cache(maxsize=4)
def get_settings(_env_file: str | None = ".env") -> Settings:
    return load_settings(_env_file)


def reset_settings_cache() -> None:
    get_settings.cache_clear()
