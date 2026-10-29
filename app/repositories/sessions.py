from __future__ import annotations

from datetime import UTC, datetime

from app.core.config import Settings
from app.repositories.database import transaction
from app.services.auth import AuthenticatedUser


def _utc_naive(timestamp: float) -> datetime:
    return datetime.fromtimestamp(timestamp, tz=UTC).replace(tzinfo=None)


class SessionRepository:
    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings

    def create(self, user_id: int, token_hash: str, expires_at: float) -> None:
        with transaction(self.settings) as conn, conn.cursor() as cursor:
            cursor.execute(
                "INSERT INTO sessions (token_hash, user_id, expires_at) VALUES (%s, %s, %s)",
                (token_hash, user_id, _utc_naive(expires_at)),
            )

    def get_user_by_token_hash(
        self, token_hash: str, now: float
    ) -> AuthenticatedUser | None:
        with transaction(self.settings) as conn, conn.cursor() as cursor:
            cursor.execute(
                """
                    SELECT users.id, users.username
                    FROM sessions
                    JOIN users ON users.id = sessions.user_id
                    WHERE sessions.token_hash = %s AND sessions.expires_at > %s
                    """,
                (token_hash, _utc_naive(now)),
            )
            row = cursor.fetchone()
        if row is None:
            return None
        return AuthenticatedUser(id=row["id"], username=row["username"])

    def delete(self, token_hash: str) -> None:
        with transaction(self.settings) as conn, conn.cursor() as cursor:
            cursor.execute("DELETE FROM sessions WHERE token_hash = %s", (token_hash,))

    def delete_for_user(self, user_id: int) -> None:
        with transaction(self.settings) as conn, conn.cursor() as cursor:
            cursor.execute("DELETE FROM sessions WHERE user_id = %s", (user_id,))

    def prune_expired(self, now: float) -> None:
        with transaction(self.settings) as conn, conn.cursor() as cursor:
            cursor.execute("DELETE FROM sessions WHERE expires_at <= %s", (_utc_naive(now),))
