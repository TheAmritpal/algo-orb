import re

import pymysql
import pytest
from starlette.testclient import TestClient

from app.core.config import Settings

from .fakes import build_test_app, make_user_store

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


@pytest.fixture
def client(settings: Settings) -> TestClient:
    with TestClient(build_test_app(settings)) as test_client:
        yield test_client


class FailingSessionStore:
    def create(self, user_id, token_hash, expires_at):
        raise pymysql.err.OperationalError(2003, "database down")

    def get_user_by_token_hash(self, token_hash, now):
        raise pymysql.err.OperationalError(2003, "database down")

    def delete(self, token_hash):
        raise pymysql.err.OperationalError(2003, "database down")

    def delete_for_user(self, user_id):
        raise pymysql.err.OperationalError(2003, "database down")

    def prune_expired(self, now):
        raise pymysql.err.OperationalError(2003, "database down")


@pytest.fixture
def failing_client(settings: Settings) -> TestClient:
    users = make_user_store()
    with TestClient(
        build_test_app(settings, users=users, sessions=FailingSessionStore())
    ) as test_client:
        yield test_client


@pytest.fixture
def login(client: TestClient):
    def perform() -> None:
        page = client.get("/login")
        match = re.search(r'name="csrf_token" value="([^"]+)"', page.text)
        assert match, "login form must render a CSRF token"
        response = client.post(
            "/login",
            data={"username": "admin", "password": "pw", "csrf_token": match.group(1)},
            follow_redirects=False,
        )
        assert response.status_code == 303

    return perform
