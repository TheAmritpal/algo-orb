"""Password and browser-token primitives; never log their inputs."""

import hashlib
import hmac
import re
import secrets
import time

from pwdlib import PasswordHash
from pwdlib.exceptions import UnknownHashError

password_hash = PasswordHash.recommended()
DUMMY_HASH = password_hash.hash(secrets.token_urlsafe(32))
TOKEN_PATTERN = re.compile(r"[A-Za-z0-9_-]{43}", re.ASCII)


def hash_password(password: str) -> str:
    return password_hash.hash(password)


def verify_password(password: str, encoded: str | None) -> bool:
    try:
        valid = password_hash.verify(password, encoded or DUMMY_HASH)
        return bool(encoded) and valid
    except (UnknownHashError, ValueError):
        password_hash.verify(password, DUMMY_HASH)
        return False


def new_token() -> str:
    return secrets.token_urlsafe(32)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def new_login_csrf(secret: str) -> str:
    payload = f"{int(time.time())}.{new_token()}"
    signature = hmac.new(secret.encode(), payload.encode(), hashlib.sha256).hexdigest()
    return f"{payload}.{signature}"


def valid_login_csrf(token: str, cookie: str, secret: str) -> bool:
    if (
        not token
        or len(token) > 256
        or not hmac.compare_digest(token.encode(), cookie.encode())
    ):
        return False
    try:
        timestamp, nonce, signature = token.split(".")
        age = time.time() - int(timestamp)
    except ValueError:
        return False
    if not TOKEN_PATTERN.fullmatch(nonce):
        return False
    payload = f"{timestamp}.{nonce}"
    expected = hmac.new(secret.encode(), payload.encode(), hashlib.sha256).hexdigest()
    return 0 <= age <= 600 and hmac.compare_digest(
        signature.encode(), expected.encode()
    )
