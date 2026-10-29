import pytest

from app.core.config import Settings

BASE = {
    "app_session_secret": "s" * 32,
    "app_username": "admin",
    "app_password": "pw",
    "mysql_host": "127.0.0.1",
    "mysql_user": "app",
    "mysql_password": "db",
    "mysql_database": "algo_orb",
    "upstox_sandbox_api_key": "sandbox-key",
    "upstox_sandbox_api_secret": "sandbox-secret",
    "upstox_sandbox_redirect_uri": "http://localhost:8000/auth/upstox/callback",
    "upstox_live_api_key": "live-key",
    "upstox_live_api_secret": "live-secret",
    "upstox_live_redirect_uri": "https://example.com/auth/upstox/callback",
    "upstox_account_user_id": "acct",
    "order_max_notional": "10000",
    "order_max_quantity": 1000,
}


class FrozenClock:
    def __init__(self, now: float = 1_700_000_000.0) -> None:
        self._now = now

    def time(self) -> float:
        return self._now

    def advance(self, seconds: float) -> None:
        self._now += seconds


@pytest.fixture
def clock() -> FrozenClock:
    return FrozenClock()


@pytest.fixture
def settings() -> Settings:
    return Settings(_env_file=None, **BASE)
