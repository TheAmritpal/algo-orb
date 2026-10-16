"""Persist the current access token for each Upstox app and user."""

from app.database import get_connection


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
