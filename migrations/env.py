"""Alembic environment using the application's MYSQL_* configuration."""

from logging.config import fileConfig

from alembic import context
from sqlalchemy import create_engine, pool

from app.repositories.database import get_sqlalchemy_url

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name, disable_existing_loggers=False)

# Migrations are explicitly authored; there are no ORM models to autogenerate.
target_metadata = None


def run_migrations_offline() -> None:
    # SQL generation needs neither credentials nor a live MySQL connection.
    context.configure(
        dialect_name="mysql",
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    engine = create_engine(
        get_sqlalchemy_url(),
        poolclass=pool.NullPool,
        hide_parameters=True,
        connect_args={"connect_timeout": 5},
    )
    try:
        with engine.connect() as connection:
            context.configure(connection=connection, target_metadata=target_metadata)
            with context.begin_transaction():
                context.run_migrations()
    finally:
        engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
