from __future__ import annotations

from dataclasses import dataclass, field

from app.core.errors import UpstoxOAuthError
from app.core.security import hash_password
from app.services.auth import AuthenticatedUser, User


class FakeUserStore:
    def __init__(self) -> None:
        self.rows: dict[str, User] = {}
        self._next_id = 1

    def get_by_username(self, username: str) -> User | None:
        return self.rows.get(username)

    def upsert(self, username: str, password_hash: str) -> User:
        existing = self.rows.get(username)
        user_id = existing.id if existing else self._next_id
        if existing is None:
            self._next_id += 1
        user = User(id=user_id, username=username, password_hash=password_hash)
        self.rows[username] = user
        return user


@dataclass
class _SessionRow:
    user_id: int
    expires_at: float


class FakeSessionStore:
    def __init__(self, users: FakeUserStore) -> None:
        self.users = users
        self.rows: dict[str, _SessionRow] = {}
        self.next_id = 1

    def create(self, user_id: int, token_hash: str, expires_at: float) -> None:
        self.rows[token_hash] = _SessionRow(user_id=user_id, expires_at=expires_at)

    def get_user_by_token_hash(self, token_hash: str, now: float) -> AuthenticatedUser | None:
        row = self.rows.get(token_hash)
        if row is None or row.expires_at <= now:
            return None
        for user in self.users.rows.values():
            if user.id == row.user_id:
                return AuthenticatedUser(id=user.id, username=user.username)
        return None

    def delete(self, token_hash: str) -> None:
        self.rows.pop(token_hash, None)

    def delete_for_user(self, user_id: int) -> None:
        self.rows = {key: row for key, row in self.rows.items() if row.user_id != user_id}

    def prune_expired(self, now: float) -> None:
        self.rows = {key: row for key, row in self.rows.items() if row.expires_at > now}


class FakeTokenStore:
    def __init__(self) -> None:
        self.rows: dict[tuple[str, str], str] = {}

    def get(self, client_id: str, user_id: str) -> str | None:
        return self.rows.get((client_id, user_id))

    def upsert(self, client_id: str, user_id: str, access_token: str) -> None:
        self.rows[(client_id, user_id)] = access_token

    def delete(self, client_id: str, user_id: str) -> None:
        self.rows.pop((client_id, user_id), None)


class FakeUpstoxSdk:
    def __init__(self, user_id: str = "acct") -> None:
        self.user_id = user_id
        self.exchanges: list[str] = []
        self.fail = False

    def authorization_url(self, state: str) -> str:
        return f"https://example.test/auth?state={state}"

    def exchange_code(self, code: str) -> str:
        if self.fail:
            raise UpstoxOAuthError("exchange failed")
        self.exchanges.append(code)
        return "token-1"

    def profile(self, access_token: str) -> str:
        if self.fail:
            raise UpstoxOAuthError("profile failed")
        return self.user_id


def make_user_store(password: str = "pw") -> FakeUserStore:
    users = FakeUserStore()
    users.upsert("admin", hash_password(password))
    return users


def build_test_app(settings, *, users=None, sessions=None, tokens=None, sdk=None):
    from app.main import create_app

    resolved_users = users or make_user_store()
    resolved_sessions = sessions or FakeSessionStore(resolved_users)
    resolved_tokens = tokens or FakeTokenStore()
    resolved_sdk = sdk or FakeUpstoxSdk(settings.upstox_account_user_id)
    return create_app(
        settings,
        users=resolved_users,
        sessions=resolved_sessions,
        tokens=resolved_tokens,
        sdk=resolved_sdk,
    )


@dataclass
class FakeAuditStore:
    rows: dict[str, list[str]] = field(default_factory=dict)

    def statuses(self, client_order_id: str) -> list[str]:
        return self.rows.get(client_order_id, [])
