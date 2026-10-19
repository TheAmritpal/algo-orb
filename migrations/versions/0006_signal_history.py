"""Record observed candle decisions without changing daily entry allowance."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mysql

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade():
    text = lambda n: mysql.VARCHAR(n, charset="ascii", collation="ascii_bin")
    op.create_table(
        "strategy_signals",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "run_id", sa.Integer(), sa.ForeignKey("strategy_runs.id"), nullable=False
        ),
        sa.Column("observed_at", mysql.DATETIME(fsp=6), nullable=False),
        sa.Column("candle_start", mysql.DATETIME(fsp=6), nullable=False),
        sa.Column("candle_close", mysql.DATETIME(fsp=6), nullable=False),
        sa.Column("close_price", mysql.DECIMAL(20, 8), nullable=False),
        sa.Column("opening_high", mysql.DECIMAL(20, 8), nullable=False),
        sa.Column("opening_low", mysql.DECIMAL(20, 8), nullable=False),
        sa.Column("direction", text(2)),
        sa.Column("outcome", text(16), nullable=False),
        sa.UniqueConstraint("run_id", "candle_close", name="uq_strategy_signal_candle"),
        mysql_engine="InnoDB",
    )
    op.create_index(
        "ix_strategy_history_account",
        "strategy_runs",
        ["client_id", "user_id", "mode", "exchange_date", "id"],
    )


def downgrade():
    op.drop_index("ix_strategy_history_account", table_name="strategy_runs")
    op.drop_table("strategy_signals")
