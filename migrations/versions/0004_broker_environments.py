"""Isolate sandbox/live credentials and record sandbox order probes."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mysql

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "upstox_tokens",
        sa.Column(
            "environment",
            mysql.VARCHAR(8, charset="ascii", collation="ascii_bin"),
            nullable=False,
            server_default="live",
        ),
    )
    # One ALTER preserves uniqueness throughout the primary-key replacement.
    op.execute(
        "ALTER TABLE upstox_tokens DROP PRIMARY KEY, ADD PRIMARY KEY (client_id, user_id, environment)"
    )
    op.create_table(
        "sandbox_order_tests",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "client_id",
            mysql.VARCHAR(255, charset="ascii", collation="ascii_bin"),
            nullable=False,
        ),
        sa.Column(
            "user_id",
            mysql.VARCHAR(100, charset="ascii", collation="ascii_bin"),
            nullable=False,
        ),
        sa.Column("request", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("order_id", sa.String(100)),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column(
            "updated_at",
            sa.TIMESTAMP(),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP"),
        ),
        mysql_engine="InnoDB",
    )
    op.create_table(
        "sandbox_test_events",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "test_id",
            sa.Integer(),
            sa.ForeignKey("sandbox_order_tests.id"),
            nullable=False,
        ),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("order_id", sa.String(100)),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        mysql_engine="InnoDB",
    )


def downgrade():
    # Do not silently destroy tokens or test evidence on downgrade.
    raise RuntimeError(
        "Broker environment downgrade requires an explicit backup and manual reconciliation"
    )
