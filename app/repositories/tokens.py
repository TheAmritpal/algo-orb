"""Persist tokens isolated by broker environment, app and account."""

from dataclasses import dataclass, field

from app.repositories.database import get_connection


def validate_environment(environment: str) -> None:
    if environment not in {"sandbox", "live"}:
        raise ValueError("Broker environment must be sandbox or live")


def save_token(
    client_id: str, user_id: str, access_token: str, *, environment: str = "live"
) -> None:
    validate_environment(environment)
    if not all(
        isinstance(v, str) and v.strip() for v in (client_id, user_id, access_token)
    ):
        raise ValueError("App, account and token are required")
    connection = get_connection()
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                """INSERT INTO upstox_tokens (client_id, user_id, access_token, environment)
                   VALUES (%s, %s, %s, %s)
                   ON DUPLICATE KEY UPDATE access_token = %s,
                       updated_at = CURRENT_TIMESTAMP""",
                (client_id, user_id, access_token, environment, access_token),
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


def get_latest_token(
    client_id: str, *, environment: str = "live"
) -> UpstoxToken | None:
    """Read the most recently connected account within one environment."""
    validate_environment(environment)
    connection = get_connection()
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                """SELECT user_id, access_token FROM upstox_tokens
                   WHERE client_id = %s AND environment = %s
                   ORDER BY updated_at DESC, created_at DESC, user_id ASC LIMIT 1""",
                (client_id, environment),
            )
            row = cursor.fetchone()
            return UpstoxToken(*row) if row else None
    finally:
        connection.close()


def get_token(
    client_id: str, user_id: str, *, environment: str = "live"
) -> UpstoxToken | None:
    """Read the selected account and environment; never fall back."""
    validate_environment(environment)
    connection = get_connection()
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT user_id, access_token FROM upstox_tokens WHERE client_id=%s AND user_id=%s AND environment=%s",
                (client_id, user_id, environment),
            )
            row = cursor.fetchone()
            return UpstoxToken(*row) if row else None
    finally:
        connection.close()


def delete_token(
    client_id: str, user_id: str, access_token: str, *, environment: str = "live"
) -> None:
    """Remove only the revoked token; preserve any concurrently saved replacement."""
    validate_environment(environment)
    connection = get_connection()
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                """DELETE FROM upstox_tokens
                   WHERE client_id=%s AND user_id=%s AND environment=%s
                   AND BINARY access_token = BINARY %s""",
                (client_id, user_id, environment, access_token),
            )
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()
