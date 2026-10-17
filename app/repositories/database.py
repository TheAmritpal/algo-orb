"""On-demand MySQL connections; callers must close returned connections."""

import pymysql
from sqlalchemy.engine import URL

from app.core.config import get_mysql_config


def get_connection():
    return pymysql.connect(
        **get_mysql_config(), connect_timeout=5, read_timeout=5, write_timeout=5
    )


def get_sqlalchemy_url() -> URL:
    """Build the migration URL without interpolating or logging credentials."""
    settings = get_mysql_config()
    return URL.create(
        "mysql+pymysql",
        username=settings["user"],
        password=settings["password"],
        host=settings["host"],
        port=settings["port"],
        database=settings["database"],
    )
