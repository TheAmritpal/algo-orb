from app.cli.sync_admin import run
from app.core.security import verify_password
from tests.fakes import FakeSessionStore, FakeUserStore


def test_creates_admin_and_hashes_password(settings):
    users = FakeUserStore()
    sessions = FakeSessionStore(users)
    assert run(settings, users, sessions) == 0
    user = users.get_by_username("admin")
    assert user is not None
    assert verify_password("pw", user.password_hash)


def test_is_idempotent_when_password_matches(settings):
    users = FakeUserStore()
    sessions = FakeSessionStore(users)
    run(settings, users, sessions)
    before = users.get_by_username("admin").password_hash
    assert run(settings, users, sessions) == 0
    assert users.get_by_username("admin").password_hash == before


def test_password_change_revokes_sessions(settings):
    users = FakeUserStore()
    sessions = FakeSessionStore(users)
    run(settings, users, sessions)
    user = users.get_by_username("admin")
    sessions.create(user.id, "a" * 64, expires_at=9_999_999_999)
    changed = settings.model_copy(update={"app_password": "new-pw"})
    assert run(changed, users, sessions) == 0
    assert sessions.get_user_by_token_hash("a" * 64, now=0) is None
