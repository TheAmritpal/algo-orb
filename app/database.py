"""On-demand MySQL connections; callers must close returned connections."""

import pymysql

from app.config import get_mysql_config


def get_connection():
    return pymysql.connect(**get_mysql_config())
