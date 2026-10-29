import os
import secrets

import pytest

from app.core.config import Settings
from app.repositories.users import UserRepository
from tests.conftest import BASE


@pytest.mark.mysql
def test_upsert_and_get_user():
    database = os.environ.get("MYSQL_TEST_DATABASE")
    if not database:
        pytest.skip("MYSQL_TEST_DATABASE is not configured")
    settings = Settings(_env_file=None, **{**BASE, "mysql_database": database})
    repository = UserRepository(settings)
    username = f"test_{secrets.token_hex(4)}"
    created = repository.upsert(username, "hash-one")
    assert repository.get_by_username(username).password_hash == "hash-one"
    updated = repository.upsert(username, "hash-two")
    assert updated.id == created.id
    assert repository.get_by_username(username).password_hash == "hash-two"
