from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

from app.core.errors import AuthError
from app.core.security import (
    LoginThrottle,
    hash_session_token,
    new_session_token,
    verify_password,
)


@dataclass(frozen=True)
class User:
    id: int
    username: str
    password_hash: str


@dataclass(frozen=True)
class AuthenticatedUser:
    id: int
    username: str


class UserStore(Protocol):
    def get_by_username(self, username: str) -> User | None: ...

    def upsert(self, username: str, password_hash: str) -> User: ...


class SessionStore(Protocol):
    def create(self, user_id: int, token_hash: str, expires_at: float) -> None: ...

    def get_user_by_token_hash(
        self, token_hash: str, now: float
    ) -> AuthenticatedUser | None: ...

    def delete(self, token_hash: str) -> None: ...

    def delete_for_user(self, user_id: int) -> None: ...

    def prune_expired(self, now: float) -> None: ...


class AuthService:
    def __init__(
        self,
        users: UserStore,
        sessions: SessionStore,
        session_ttl_seconds: int = 43_200,
        clock: Callable[[], float] = time.time,
        throttle: LoginThrottle | None = None,
    ) -> None:
        self.users = users
        self.sessions = sessions
        self.session_ttl_seconds = session_ttl_seconds
        self.clock = clock
        self.throttle = throttle or LoginThrottle(max_attempts=5, window_seconds=300, time_fn=clock)

    def login(self, username: str, password: str, client_key: str = "default") -> str:
        self.throttle.check(client_key)
        user = self.users.get_by_username(username.strip())
        if user is None or not verify_password(password, user.password_hash):
            self.throttle.record_failure(client_key)
            raise AuthError("Invalid username or password")
        token = new_session_token()
        now = self.clock()
        self.sessions.create(
            user_id=user.id,
            token_hash=hash_session_token(token),
            expires_at=now + self.session_ttl_seconds,
        )
        self.sessions.prune_expired(now)
        self.throttle.reset(client_key)
        return token

    def authenticate(self, token: str | None) -> AuthenticatedUser | None:
        if not token:
            return None
        return self.sessions.get_user_by_token_hash(
            hash_session_token(token), self.clock()
        )

    def logout(self, token: str | None) -> None:
        if token:
            self.sessions.delete(hash_session_token(token))
