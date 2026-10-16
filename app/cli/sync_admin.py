"""Run after migrations: uv run python -m app.cli.sync_admin."""

import sys

import pymysql

from app.core.config import get_admin_credentials
from app.core.security import hash_password
from app.repositories.users import sync_admin


def main() -> int:
    try:
        username, password = get_admin_credentials()
        sync_admin(username, hash_password(password))
    except (ValueError, RuntimeError, OSError, pymysql.MySQLError):
        print(
            "Administrator sync failed. Check APP_USERNAME, APP_PASSWORD, MYSQL_* and migrations. An existing administrator must have the same username; another sync may also be running.",
            file=sys.stderr,
        )
        return 1
    print("Administrator synchronized. Existing sessions revoked.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
