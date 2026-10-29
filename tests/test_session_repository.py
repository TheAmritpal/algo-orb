import os
import secrets

import pytest

from app.core.config import Settings
from app.repositories.sessions import SessionRepository
from app.repositories.users import UserRepository
from tests.conftest import BASE


@pytest.mark.mysql
def test_create_lookup_and_delete_session():
    database = os.environ.get("MYSQL_TEST_DATABASE")
    if not database:
        pytest.skip("MYSQL_TEST_DATABASE is not configured")
    settings = Settings(_env_file=None, **{**BASE, "mysql_database": database})
    users = UserRepository(settings)
    sessions = SessionRepository(settings)
    user = users.upsert(f"test_{secrets.token_hex(4)}", "hash")
    token_hash = secrets.token_hex(32)
    now = 1_700_000_000.0
    sessions.create(user.id, token_hash, now + 60)
    assert sessions.get_user_by_token_hash(token_hash, now).id == user.id
    assert sessions.get_user_by_token_hash(token_hash, now + 61) is None
    sessions.delete(token_hash)
    assert sessions.get_user_by_token_hash(token_hash, now) is None
