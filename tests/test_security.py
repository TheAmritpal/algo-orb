import pytest

from app.core.errors import AuthError
from app.core.security import (
    LoginThrottle,
    hash_password,
    hash_session_token,
    issue_csrf,
    new_session_token,
    sign_state,
    verify_csrf,
    verify_password,
    verify_state,
)


def test_password_round_trip():
    hashed = hash_password("correct horse battery staple")
    assert verify_password("correct horse battery staple", hashed)
    assert not verify_password("wrong", hashed)


def test_session_token_is_random_and_hashed():
    first, second = new_session_token(), new_session_token()
    assert first != second
    assert hash_session_token(first) == hash_session_token(first)
    assert hash_session_token(first) != first
    assert len(hash_session_token(first)) == 64


def test_csrf_rejects_tampering():
    token = issue_csrf("s" * 32)
    assert verify_csrf("s" * 32, token)
    assert not verify_csrf("s" * 32, token + "x")
    assert not verify_csrf("s" * 32, "not-a-token")


def test_state_rejects_expiry(clock):
    now = clock.time()
    token = sign_state("s" * 32, {"n": "x"}, ttl_seconds=10, now=now)
    assert verify_state("s" * 32, token, now=now)["n"] == "x"
    with pytest.raises(AuthError):
        verify_state("s" * 32, token, now=now + 11)


def test_throttle_blocks_after_limit(clock):
    throttle = LoginThrottle(max_attempts=2, window_seconds=60, time_fn=clock.time)
    throttle.record_failure("ip")
    throttle.record_failure("ip")
    with pytest.raises(AuthError):
        throttle.check("ip")
    clock.advance(61)
    throttle.check("ip")
