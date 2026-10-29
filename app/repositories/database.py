from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager

import pymysql
from pymysql.cursors import DictCursor
from sqlalchemy.engine import URL

from app.core.config import DatabaseSettings, Settings, get_settings


def get_connection(settings: Settings | None = None) -> pymysql.Connection:
    resolved = settings or get_settings()
    return pymysql.connect(
        host=resolved.mysql_host,
        port=resolved.mysql_port,
        user=resolved.mysql_user,
        password=resolved.mysql_password,
        database=resolved.mysql_database,
        cursorclass=DictCursor,
        autocommit=False,
        charset="utf8mb4",
    )


def sqlalchemy_url(settings: Settings | DatabaseSettings) -> str:
    return URL.create(
        "mysql+pymysql",
        username=settings.mysql_user,
        password=settings.mysql_password,
        host=settings.mysql_host,
        port=settings.mysql_port,
        database=settings.mysql_database,
    ).render_as_string(hide_password=False)


@contextmanager
def transaction(
    settings: Settings | None = None,
    connect: Callable[[Settings | None], pymysql.Connection] | None = None,
) -> Iterator[pymysql.Connection]:
    conn = connect(settings) if connect else get_connection(settings)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
