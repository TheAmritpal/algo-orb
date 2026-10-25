"""Preserve cancelled GTT attempts and scope journals for bounded retries."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mysql

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "strategy_gtts",
        sa.Column("attempt", sa.Integer(), nullable=False, server_default="1"),
    )
    op.add_column(
        "strategy_gtts",
        sa.Column("retry_requested", sa.Boolean(), nullable=False, server_default="0"),
    )
    op.add_column(
        "strategy_gtts",
        sa.Column("retry_blocked", sa.Boolean(), nullable=False, server_default="0"),
    )
    for table in ("strategy_gtt_mutations", "strategy_gtt_legs"):
        op.add_column(
            table,
            sa.Column("attempt", sa.Integer(), nullable=False, server_default="1"),
        )
    op.create_index("ix_strategy_gtt_legs_run", "strategy_gtt_legs", ["run_id"])
    op.drop_constraint("PRIMARY", "strategy_gtt_legs", type_="primary")
    op.create_primary_key(
        "PRIMARY", "strategy_gtt_legs", ["run_id", "attempt", "strategy"]
    )
    op.create_table(
        "strategy_gtt_attempt_history",
        sa.Column(
            "run_id", sa.Integer(), sa.ForeignKey("strategy_runs.id"), primary_key=True
        ),
        sa.Column("attempt", sa.Integer(), primary_key=True),
        sa.Column("request", mysql.JSON(), nullable=False),
        sa.Column("stop", mysql.DECIMAL(20, 8), nullable=False),
        sa.Column("target", mysql.DECIMAL(20, 8), nullable=False),
        sa.Column("created_at", mysql.DATETIME(fsp=6), nullable=False),
        sa.Column(
            "broker_id",
            mysql.VARCHAR(100, charset="ascii", collation="ascii_bin"),
            nullable=False,
            unique=True,
        ),
        sa.Column("snapshot", mysql.JSON(), nullable=False),
        mysql_engine="InnoDB",
    )


def downgrade():
    if not op.get_context().as_sql:
        used = (
            op.get_bind()
            .execute(sa.text("SELECT COUNT(*) FROM strategy_gtt_attempt_history"))
            .scalar_one()
        )
        if used:
            raise RuntimeError(
                "Cannot downgrade after GTT retries: preserve attempt audit history"
            )
    op.drop_table("strategy_gtt_attempt_history")
    op.drop_constraint("PRIMARY", "strategy_gtt_legs", type_="primary")
    op.create_primary_key("PRIMARY", "strategy_gtt_legs", ["run_id", "strategy"])
    op.drop_index("ix_strategy_gtt_legs_run", table_name="strategy_gtt_legs")
    for table in ("strategy_gtt_legs", "strategy_gtt_mutations", "strategy_gtts"):
        op.drop_column(table, "attempt")
    op.drop_column("strategy_gtts", "retry_requested")
    op.drop_column("strategy_gtts", "retry_blocked")
