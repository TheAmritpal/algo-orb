from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time
from collections.abc import Callable
from typing import Any

from pwdlib import PasswordHash

from app.core.errors import AuthError

_hasher = PasswordHash.recommended()


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return _hasher.verify(password, password_hash)
    except (TypeError, ValueError):
        return False


def new_session_token() -> str:
    return secrets.token_urlsafe(32)


def hash_session_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _b64encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _b64decode(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(value + padding)


def _signature(secret: str, payload: bytes) -> str:
    return hmac.new(secret.encode("utf-8"), payload, hashlib.sha256).hexdigest()


def issue_csrf(secret: str) -> str:
    nonce = secrets.token_urlsafe(16)
    return f"{nonce}.{_signature(secret, nonce.encode('ascii'))}"


def verify_csrf(secret: str, token: str) -> bool:
    try:
        nonce, signature = token.split(".", 1)
    except (AttributeError, ValueError):
        return False
    if not nonce:
        return False
    return hmac.compare_digest(signature, _signature(secret, nonce.encode("ascii")))


def sign_state(
    secret: str,
    payload: dict[str, Any],
    ttl_seconds: int,
    now: float | None = None,
) -> str:
    issued_at = time.time() if now is None else now
    body = {**payload, "exp": issued_at + ttl_seconds}
    raw = json.dumps(body, separators=(",", ":"), sort_keys=True).encode("utf-8")
    return f"{_b64encode(raw)}.{_signature(secret, raw)}"


def verify_state(secret: str, token: str, now: float | None = None) -> dict[str, Any]:
    try:
        encoded, signature = token.split(".", 1)
        raw = _b64decode(encoded)
    except (AttributeError, TypeError, ValueError):
        raise AuthError("Invalid state") from None
    if not hmac.compare_digest(signature, _signature(secret, raw)):
        raise AuthError("Invalid state")
    try:
        body = json.loads(raw)
    except ValueError:
        raise AuthError("Invalid state") from None
    if not isinstance(body, dict):
        raise AuthError("Invalid state")
    current = time.time() if now is None else now
    if float(body.get("exp", 0)) < current:
        raise AuthError("State expired")
    return body


class LoginThrottle:
    def __init__(
        self,
        max_attempts: int,
        window_seconds: int,
        time_fn: Callable[[], float] = time.time,
    ) -> None:
        self.max_attempts = max_attempts
        self.window_seconds = window_seconds
        self._time = time_fn
        self._failures: dict[str, list[float]] = {}

    def _recent(self, key: str) -> list[float]:
        current = self._time()
        recent = [stamp for stamp in self._failures.get(key, []) if current - stamp < self.window_seconds]
        self._failures[key] = recent
        return recent

    def check(self, key: str) -> None:
        if len(self._recent(key)) >= self.max_attempts:
            raise AuthError("Too many attempts")

    def record_failure(self, key: str) -> None:
        self._recent(key).append(self._time())

    def reset(self, key: str) -> None:
        self._failures.pop(key, None)
