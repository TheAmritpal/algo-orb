"""Persist the current access token for each Upstox app and user."""

from dataclasses import dataclass, field

from app.repositories.database import get_connection


def save_token(client_id: str, user_id: str, access_token: str) -> None:
    connection = get_connection()
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                """INSERT INTO upstox_tokens (client_id, user_id, access_token)
                   VALUES (%s, %s, %s)
                   ON DUPLICATE KEY UPDATE access_token = %s,
                       updated_at = CURRENT_TIMESTAMP""",
                (client_id, user_id, access_token, access_token),
            )
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


@dataclass(frozen=True)
class UpstoxToken:
    user_id: str
    access_token: str = field(repr=False)


def get_latest_token(client_id: str) -> UpstoxToken | None:
    """Read the most recently connected broker account for this single-admin app."""
    connection = get_connection()
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                """SELECT user_id, access_token FROM upstox_tokens
                   WHERE client_id = %s
                   ORDER BY updated_at DESC, created_at DESC, user_id ASC LIMIT 1""",
                (client_id,),
            )
            row = cursor.fetchone()
            return UpstoxToken(*row) if row else None
    finally:
        connection.close()
