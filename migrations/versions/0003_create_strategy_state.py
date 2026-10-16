"""Persist RELIANCE strategy runs, intents, unique fills and sanitized events."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mysql

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def schema():
    m = sa.MetaData()
    text = lambda n: mysql.VARCHAR(n, charset="ascii", collation="ascii_bin")
    integer = lambda name, default="0": sa.Column(
        name, sa.Integer(), nullable=False, server_default=default
    )
    dec = lambda name: sa.Column(name, mysql.DECIMAL(20, 8))
    sa.Table(
        "strategy_runs",
        m,
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("client_id", text(255), nullable=False),
        sa.Column("user_id", text(100), nullable=False),
        sa.Column("strategy", text(32), nullable=False),
        sa.Column("mode", text(8), nullable=False),
        sa.Column("exchange_date", sa.Date(), nullable=False),
        integer("version"),
        sa.Column("state", text(32), nullable=False, server_default="waiting_range"),
        integer("entry_reserved"),
        dec("opening_high"),
        dec("opening_low"),
        sa.Column("processed_candle", mysql.DATETIME(fsp=6)),
        sa.Column("contract", mysql.JSON()),
        integer("requested_quantity"),
        integer("filled_quantity"),
        integer("remaining_quantity"),
        dec("entry_average"),
        dec("stop"),
        dec("target"),
        sa.Column("heartbeat", mysql.DATETIME(fsp=6)),
        sa.Column("message", sa.String(255), nullable=False, server_default=""),
        sa.UniqueConstraint(
            "client_id",
            "user_id",
            "strategy",
            "mode",
            "exchange_date",
            name="uq_strategy_day",
        ),
        mysql_engine="InnoDB",
    )
    sa.Table(
        "strategy_orders",
        m,
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "run_id", sa.Integer(), sa.ForeignKey("strategy_runs.id"), nullable=False
        ),
        sa.Column("purpose", text(8), nullable=False),
        sa.Column("tag", text(20), nullable=False, unique=True),
        sa.Column("request", mysql.JSON(), nullable=False),
        sa.Column("submitted_at", mysql.DATETIME(fsp=6), nullable=False),
        sa.Column("broker_id", text(100)),
        sa.Column("status", text(32), nullable=False, server_default="unknown"),
        mysql_engine="InnoDB",
    )
    sa.Table(
        "strategy_fills",
        m,
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "intent_id",
            sa.Integer(),
            sa.ForeignKey("strategy_orders.id"),
            nullable=False,
        ),
        sa.Column("client_id", text(255), nullable=False),
        sa.Column("user_id", text(100), nullable=False),
        sa.Column("mode", text(8), nullable=False),
        sa.Column("trade_id", text(100), nullable=False),
        sa.Column("order_id", text(100), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("price", mysql.DECIMAL(20, 8), nullable=False),
        sa.UniqueConstraint(
            "client_id", "user_id", "mode", "trade_id", name="uq_strategy_trade"
        ),
        mysql_engine="InnoDB",
    )
    sa.Table(
        "strategy_events",
        m,
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "run_id", sa.Integer(), sa.ForeignKey("strategy_runs.id"), nullable=False
        ),
        sa.Column("created_at", mysql.DATETIME(fsp=6), nullable=False),
        sa.Column("kind", text(32), nullable=False),
        sa.Column("fields", mysql.JSON(), nullable=False),
        mysql_engine="InnoDB",
    )
    return m


def upgrade():
    metadata = schema()
    inspector = None if op.get_context().as_sql else sa.inspect(op.get_bind())
    for table in metadata.sorted_tables:
        if inspector and inspector.has_table(table.name):
            actual = inspector.get_columns(table.name)
            expected = list(table.columns)
            dialect = op.get_bind().dialect
            if [
                (
                    c["name"],
                    str(c["type"].compile(dialect=dialect)).upper(),
                    c["nullable"],
                )
                for c in actual
            ] != [
                (c.name, str(c.type.compile(dialect=dialect)).upper(), c.nullable)
                for c in expected
            ]:
                raise RuntimeError("Existing strategy schema does not match migration")
            want = {
                tuple(c.columns.keys())
                for c in table.constraints
                if isinstance(c, sa.UniqueConstraint)
            }
            got = {
                tuple(c["column_names"])
                for c in inspector.get_unique_constraints(table.name)
            }
            pk = inspector.get_pk_constraint(table.name).get("constrained_columns")
            fks = {
                (tuple(c["constrained_columns"]), c["referred_table"])
                for c in inspector.get_foreign_keys(table.name)
            }
            expected_fks = {
                (tuple(c.columns.keys()), next(iter(c.elements)).column.table.name)
                for c in table.constraints
                if isinstance(c, sa.ForeignKeyConstraint)
            }
            if (
                want != got
                or pk != ["id"]
                or fks != expected_fks
                or inspector.get_table_options(table.name)
                .get("mysql_engine", "")
                .lower()
                != "innodb"
            ):
                raise RuntimeError(
                    "Existing strategy keys or engine do not match migration"
                )
            continue
        op.execute(sa.schema.CreateTable(table))


def downgrade():
    for name in (
        "strategy_events",
        "strategy_fills",
        "strategy_orders",
        "strategy_runs",
    ):
        op.drop_table(name)
