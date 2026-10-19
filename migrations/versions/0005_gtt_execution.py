"""Persist GTT parents, mutation intents and generated order identities."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mysql

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade():
    text = lambda n: mysql.VARCHAR(n, charset="ascii", collation="ascii_bin")
    op.add_column(
        "strategy_runs",
        sa.Column(
            "execution_backend", text(8), nullable=False, server_default="orders"
        ),
    )
    op.create_table(
        "strategy_gtts",
        sa.Column(
            "run_id", sa.Integer(), sa.ForeignKey("strategy_runs.id"), primary_key=True
        ),
        sa.Column("request", mysql.JSON(), nullable=False),
        sa.Column("stop", mysql.DECIMAL(20, 8), nullable=False),
        sa.Column("target", mysql.DECIMAL(20, 8), nullable=False),
        sa.Column("created_at", mysql.DATETIME(fsp=6), nullable=False),
        sa.Column("broker_id", text(100), unique=True),
        sa.Column("snapshot", mysql.JSON()),
        mysql_engine="InnoDB",
    )
    op.create_table(
        "strategy_gtt_mutations",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "run_id",
            sa.Integer(),
            sa.ForeignKey("strategy_gtts.run_id"),
            nullable=False,
        ),
        sa.Column("kind", text(16), nullable=False),
        sa.Column("fields", mysql.JSON(), nullable=False),
        sa.Column("status", text(16), nullable=False, server_default="pending"),
        sa.Column("created_at", mysql.DATETIME(fsp=6), nullable=False),
        mysql_engine="InnoDB",
    )
    op.create_table(
        "strategy_gtt_legs",
        sa.Column(
            "run_id",
            sa.Integer(),
            sa.ForeignKey("strategy_gtts.run_id"),
            primary_key=True,
        ),
        sa.Column("strategy", text(8), primary_key=True),
        sa.Column("order_id", text(100), nullable=False),
        sa.Column(
            "intent_id",
            sa.Integer(),
            sa.ForeignKey("strategy_orders.id"),
            nullable=False,
            unique=True,
        ),
        mysql_engine="InnoDB",
    )


def downgrade():
    op.drop_table("strategy_gtt_legs")
    op.drop_table("strategy_gtt_mutations")
    op.drop_table("strategy_gtts")
    op.drop_column("strategy_runs", "execution_backend")
