from __future__ import annotations

from app.core.config import Settings
from app.repositories.database import transaction
from app.services.auth import User


class UserRepository:
    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings

    def get_by_username(self, username: str) -> User | None:
        with transaction(self.settings) as conn, conn.cursor() as cursor:
            cursor.execute(
                "SELECT id, username, password_hash FROM users WHERE username = %s",
                (username,),
            )
            row = cursor.fetchone()
        if row is None:
            return None
        return User(id=row["id"], username=row["username"], password_hash=row["password_hash"])

    def upsert(self, username: str, password_hash: str) -> User:
        with transaction(self.settings) as conn, conn.cursor() as cursor:
            cursor.execute(
                """
                    INSERT INTO users (username, password_hash)
                    VALUES (%s, %s)
                    ON DUPLICATE KEY UPDATE
                        id = LAST_INSERT_ID(id),
                        password_hash = VALUES(password_hash),
                        updated_at = CURRENT_TIMESTAMP(3)
                    """,
                (username, password_hash),
            )
            user_id = cursor.lastrowid
            cursor.execute(
                "SELECT id, username, password_hash FROM users WHERE id = %s",
                (user_id,),
            )
            row = cursor.fetchone()
        if row is None:
            raise RuntimeError("User upsert did not return a row")
        return User(id=row["id"], username=row["username"], password_hash=row["password_hash"])
