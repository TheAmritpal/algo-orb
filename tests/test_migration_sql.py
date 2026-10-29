import os
import subprocess

from tests.conftest import BASE


def test_offline_migration_creates_four_tables():
    env = {
        **os.environ,
        "MYSQL_HOST": BASE["mysql_host"],
        "MYSQL_PORT": "3306",
        "MYSQL_USER": BASE["mysql_user"],
        "MYSQL_PASSWORD": BASE["mysql_password"],
        "MYSQL_DATABASE": BASE["mysql_database"],
    }
    result = subprocess.run(
        ["uv", "run", "alembic", "upgrade", "head", "--sql"],
        check=False,
        capture_output=True,
        text=True,
        env=env,
    )
    assert result.returncode == 0, result.stderr
    for table in ("users", "sessions", "upstox_tokens", "order_audit"):
        assert f"CREATE TABLE {table}" in result.stdout
