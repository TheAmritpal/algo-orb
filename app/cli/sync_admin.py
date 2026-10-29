from __future__ import annotations

from app.core.config import Settings, get_settings
from app.core.errors import AppError
from app.core.security import hash_password, verify_password
from app.repositories.sessions import SessionRepository
from app.repositories.users import UserRepository
from app.services.auth import SessionStore, UserStore


def run(settings: Settings, users: UserStore, sessions: SessionStore) -> int:
    user = users.get_by_username(settings.app_username)
    if user is None:
        users.upsert(settings.app_username, hash_password(settings.app_password))
        return 0
    if verify_password(settings.app_password, user.password_hash):
        return 0
    users.upsert(settings.app_username, hash_password(settings.app_password))
    sessions.delete_for_user(user.id)
    return 0


def main() -> int:
    try:
        settings = get_settings()
        run(
            settings,
            users=UserRepository(settings),
            sessions=SessionRepository(settings),
        )
    except AppError as exc:
        print(f"sync_admin failed: {exc.public_message}")
        return 1
    print("admin user ready")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
