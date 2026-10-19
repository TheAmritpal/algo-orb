"""Store execution mode and explicit live enablement in MySQL."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mysql

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade():
    table = op.create_table(
        "trading_config",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=False),
        sa.Column(
            "mode", mysql.ENUM("paper", "live"), nullable=False, server_default="paper"
        ),
        sa.Column(
            "live_enabled", sa.Boolean(), nullable=False, server_default=sa.text("0")
        ),
        sa.CheckConstraint("id = 1", name="ck_trading_config_singleton"),
        sa.CheckConstraint("live_enabled IN (0, 1)", name="ck_trading_config_enabled"),
        mysql_engine="InnoDB",
    )
    op.bulk_insert(table, [{"id": 1, "mode": "paper", "live_enabled": False}])


def downgrade():
    op.drop_table("trading_config")
