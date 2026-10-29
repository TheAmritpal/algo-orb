import pytest
from pydantic import ValidationError

from app.core.config import Settings, get_settings, load_settings, reset_settings_cache
from app.core.errors import ConfigError
from tests.conftest import BASE


def test_missing_secret_fails_without_echoing_values(monkeypatch):
    for key, value in BASE.items():
        monkeypatch.setenv(key.upper(), str(value))
    monkeypatch.delenv("APP_SESSION_SECRET")
    with pytest.raises(ConfigError) as caught:
        load_settings(_env_file=None)
    message = str(caught.value)
    assert "app_session_secret" in message
    assert "sandbox-secret" not in message
    assert "live-secret" not in message


def test_sandbox_selects_sandbox_keys():
    settings = Settings(_env_file=None, **BASE)
    assert (settings.upstox_api_key, settings.upstox_api_secret) == (
        "sandbox-key",
        "sandbox-secret",
    )
    assert settings.is_live is False


def test_live_selects_live_keys():
    settings = Settings(_env_file=None, **BASE, upstox_environment="live")
    assert (settings.upstox_api_key, settings.upstox_api_secret) == (
        "live-key",
        "live-secret",
    )
    assert settings.is_live is True


def test_trading_safe_defaults():
    settings = Settings(_env_file=None, **BASE)
    assert settings.trading_mode == "paper"
    assert settings.trading_enabled is False


def test_non_positive_limit_is_rejected():
    with pytest.raises(ValidationError):
        Settings(_env_file=None, **{**BASE, "order_max_quantity": 0})


def test_get_settings_is_cached(monkeypatch):
    for key, value in BASE.items():
        monkeypatch.setenv(key.upper(), str(value))
    reset_settings_cache()
    assert get_settings(_env_file=None) is get_settings(_env_file=None)
