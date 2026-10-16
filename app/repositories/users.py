"""Single-administrator provisioning and account lookup."""

from dataclasses import dataclass

from app.repositories.database import get_connection


@dataclass(frozen=True)
class User:
    id: int
    username: str
    password_hash: str


def find_user(username: str) -> User | None:
    connection = get_connection()
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT id, username, password_hash FROM users WHERE username = %s",
                (username,),
            )
            row = cursor.fetchone()
            # MySQL collations can ignore trailing spaces: require an exact match.
            return User(*row) if row and row[1] == username else None
    finally:
        connection.close()


def sync_admin(username: str, password_hash: str) -> int:
    connection = get_connection()
    locked = False
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT GET_LOCK(SHA2(CONCAT(DATABASE(), ':algo-orb-admin'), 256), 5)"
            )
            row = cursor.fetchone()
            if not row or row[0] != 1:
                raise RuntimeError("Administrator provisioning is busy; try again")
            locked = True
            connection.begin()
            cursor.execute("SELECT id, username FROM users FOR UPDATE")
            rows = cursor.fetchall()
            if rows:
                if len(rows) != 1 or rows[0][1] != username:
                    raise ValueError(
                        "Existing administrator differs; rename requires explicit database administration"
                    )
                user_id = rows[0][0]
                cursor.execute(
                    "UPDATE users SET password_hash = %s WHERE id = %s",
                    (password_hash, user_id),
                )
            else:
                cursor.execute(
                    "INSERT INTO users (username, password_hash) VALUES (%s, %s)",
                    (username, password_hash),
                )
                user_id = cursor.lastrowid
            cursor.execute("DELETE FROM sessions WHERE user_id = %s", (user_id,))
            connection.commit()
            return user_id
    except Exception:
        connection.rollback()
        raise
    finally:
        try:
            if locked:
                with connection.cursor() as cursor:
                    cursor.execute(
                        "SELECT RELEASE_LOCK(SHA2(CONCAT(DATABASE(), ':algo-orb-admin'), 256))"
                    )
        finally:
            connection.close()
