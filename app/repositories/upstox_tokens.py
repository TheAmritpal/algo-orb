from __future__ import annotations

from app.core.config import Settings
from app.repositories.database import transaction


class TokenRepository:
    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings

    def get(self, client_id: str, user_id: str) -> str | None:
        with transaction(self.settings) as conn, conn.cursor() as cursor:
            cursor.execute(
                "SELECT access_token FROM upstox_tokens WHERE client_id = %s AND user_id = %s",
                (client_id, user_id),
            )
            row = cursor.fetchone()
        return None if row is None else str(row["access_token"])

    def upsert(self, client_id: str, user_id: str, access_token: str) -> None:
        with transaction(self.settings) as conn, conn.cursor() as cursor:
            cursor.execute(
                """
                    INSERT INTO upstox_tokens (client_id, user_id, access_token)
                    VALUES (%s, %s, %s)
                    ON DUPLICATE KEY UPDATE
                        access_token = VALUES(access_token),
                        updated_at = CURRENT_TIMESTAMP(3)
                    """,
                (client_id, user_id, access_token),
            )

    def delete(self, client_id: str, user_id: str) -> None:
        with transaction(self.settings) as conn, conn.cursor() as cursor:
            cursor.execute(
                "DELETE FROM upstox_tokens WHERE client_id = %s AND user_id = %s",
                (client_id, user_id),
            )
