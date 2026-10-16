"""Website authentication policy and bounded, single-process login limiting."""

import time
from datetime import UTC, datetime, timedelta
from threading import Lock

import pymysql
from fastapi import HTTPException, Request

from app.core.config import get_auth_settings
from app.core.security import TOKEN_PATTERN, hash_token, new_token, verify_password
from app.repositories import sessions, users
from app.repositories.sessions import Session
from app.repositories.users import User

SESSION_COOKIE = "orb_session"
LOGIN_CSRF_COOKIE = "orb_login_csrf"
SESSION_TTL = 8 * 60 * 60
BACKEND_ERRORS = (pymysql.MySQLError, ValueError, RuntimeError, OSError)


class LoginRequired(Exception):
    pass


class LoginLimiter:
    """Trust the ASGI peer address; deploy with one process or a shared limiter."""

    def __init__(self):
        self._entries: dict[str, tuple[float, int]] = {}
        self._lock = Lock()

    def _prune(self, now: float) -> None:
        for key in [
            key for key, (start, _) in self._entries.items() if now - start >= 300
        ]:
            del self._entries[key]

    def _allowed(self, client: str) -> bool:
        entry = self._entries.get(client)
        return entry[1] < 5 if entry else len(self._entries) < 10000

    def allow(self, client: str) -> bool:
        with self._lock:
            self._prune(time.monotonic())
            return self._allowed(client)

    def failed(self, client: str) -> None:
        with self._lock:
            now = time.monotonic()
            self._prune(now)
            start, count = self._entries.get(client, (now, 0))
            if client in self._entries or len(self._entries) < 10000:
                self._entries[client] = (start, count + 1)

    def reserve(self, client: str) -> bool:
        # Count in-flight attempts so concurrent requests cannot bypass the cap.
        with self._lock:
            now = time.monotonic()
            self._prune(now)
            if not self._allowed(client):
                return False
            start, count = self._entries.get(client, (now, 0))
            self._entries[client] = (start, count + 1)
            return True

    def succeeded(self, client: str) -> None:
        with self._lock:
            entry = self._entries.get(client)
            if entry:
                start, count = entry
                if count <= 1:
                    del self._entries[client]
                else:
                    self._entries[client] = (start, count - 1)


limiter = LoginLimiter()


def unavailable() -> HTTPException:
    return HTTPException(
        503,
        "Authentication is unavailable. Check application configuration, database access, and migrations.",
    )


def authenticate(username: str, password: str) -> User | None:
    user = users.find_user(username)
    valid = verify_password(password, user.password_hash if user else None)
    return user if valid else None


def issue_session(user: User) -> tuple[str, str]:
    token, csrf_token = new_token(), new_token()
    expiry = datetime.now(UTC).replace(tzinfo=None) + timedelta(seconds=SESSION_TTL)
    sessions.create_session(
        user.id, hash_token(token), csrf_token, expiry, user.password_hash
    )
    return token, csrf_token


def require_session(request: Request) -> Session:
    try:
        get_auth_settings()
        token = request.cookies.get(SESSION_COOKIE, "")
        if not TOKEN_PATTERN.fullmatch(token):
            raise LoginRequired()
        session = sessions.find_session(hash_token(token))
    except BACKEND_ERRORS:
        raise unavailable() from None
    if session is None or session.expires_at <= datetime.now(UTC).replace(tzinfo=None):
        raise LoginRequired()
    return session
