"""Durable inbox for untrusted order and GTT postbacks."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mysql

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade():
    def text(size):
        return mysql.VARCHAR(size, charset="ascii", collation="ascii_bin")

    op.create_table(
        "upstox_order_updates",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("event_hash", text(64), nullable=False),
        sa.Column("update_type", text(16), nullable=False),
        sa.Column("broker_order_id", text(100), nullable=False),
        sa.Column("user_id", text(100)),
        sa.Column("payload", mysql.JSON(), nullable=False),
        sa.Column(
            "received_at",
            mysql.TIMESTAMP(fsp=6),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP(6)"),
        ),
        sa.UniqueConstraint("event_hash", name="uq_upstox_order_update_hash"),
        mysql_engine="InnoDB",
    )
    op.create_index(
        "ix_upstox_order_update_order",
        "upstox_order_updates",
        ["update_type", "broker_order_id", "received_at"],
    )


def downgrade():
    op.drop_table("upstox_order_updates")
