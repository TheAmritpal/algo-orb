"""Revocable browser sessions; tokens are stored only as hashes."""

import hmac
from dataclasses import dataclass
from datetime import datetime

from app.repositories.database import get_connection


@dataclass(frozen=True)
class Session:
    user_id: int
    username: str
    csrf_token: str
    expires_at: datetime


class AuthenticationChanged(Exception):
    """The credential verified by the caller has since been rotated."""


def create_session(
    user_id: int,
    token_hash: str,
    csrf_token: str,
    expires_at: datetime,
    expected_password_hash: str,
) -> None:
    connection = get_connection()
    try:
        connection.begin()
        with connection.cursor() as cursor:
            # Serialize against sync_admin's row lock: rotation either removes this
            # session afterwards or this insert sees the new credential and fails.
            cursor.execute(
                "SELECT password_hash FROM users WHERE id = %s FOR UPDATE", (user_id,)
            )
            row = cursor.fetchone()
            if not row or not hmac.compare_digest(
                row[0].encode(), expected_password_hash.encode()
            ):
                raise AuthenticationChanged()
            cursor.execute("DELETE FROM sessions WHERE expires_at <= UTC_TIMESTAMP()")
            cursor.execute(
                """INSERT INTO sessions (user_id, token_hash, csrf_token, created_at, expires_at)
                   VALUES (%s, %s, %s, UTC_TIMESTAMP(), %s)""",
                (user_id, token_hash, csrf_token, expires_at),
            )
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def find_session(token_hash: str) -> Session | None:
    connection = get_connection()
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                """SELECT u.id, u.username, s.csrf_token, s.expires_at
                   FROM sessions s JOIN users u ON u.id = s.user_id
                   WHERE s.token_hash = %s AND s.expires_at > UTC_TIMESTAMP()""",
                (token_hash,),
            )
            row = cursor.fetchone()
            return Session(*row) if row else None
    finally:
        connection.close()


def delete_session(token_hash: str) -> None:
    connection = get_connection()
    try:
        with connection.cursor() as cursor:
            cursor.execute("DELETE FROM sessions WHERE token_hash = %s", (token_hash,))
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()
