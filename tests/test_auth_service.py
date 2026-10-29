import pytest

from app.core.errors import AuthError
from app.core.security import LoginThrottle, hash_session_token
from app.services.auth import AuthService
from tests.fakes import FakeSessionStore, make_user_store


def build_service(clock, password="pw"):
    users = make_user_store(password)
    sessions = FakeSessionStore(users)
    throttle = LoginThrottle(max_attempts=5, window_seconds=300, time_fn=clock.time)
    service = AuthService(
        users=users,
        sessions=sessions,
        session_ttl_seconds=43_200,
        clock=clock.time,
        throttle=throttle,
    )
    return service


def test_login_returns_token_and_stores_only_hash(clock):
    service = build_service(clock)
    token = service.login("admin", "pw")
    assert token not in service.sessions.rows
    assert hash_session_token(token) in service.sessions.rows
    assert service.authenticate(token).username == "admin"


def test_wrong_password_and_unknown_user_share_message(clock):
    service = build_service(clock)
    with pytest.raises(AuthError, match="Invalid username or password"):
        service.login("admin", "wrong")
    with pytest.raises(AuthError, match="Invalid username or password"):
        service.login("missing", "pw")


def test_expired_session_is_rejected(clock):
    service = build_service(clock)
    token = service.login("admin", "pw")
    clock.advance(43_201)
    assert service.authenticate(token) is None


def test_logout_deletes_session(clock):
    service = build_service(clock)
    token = service.login("admin", "pw")
    service.logout(token)
    assert service.authenticate(token) is None


def test_throttle_blocks_sixth_attempt(clock):
    service = build_service(clock)
    for _ in range(5):
        with pytest.raises(AuthError):
            service.login("admin", "wrong")
    with pytest.raises(AuthError, match="Too many attempts"):
        service.login("admin", "pw")
