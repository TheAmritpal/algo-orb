"""Create website users and revocable sessions.

Revision ID: 0002
Revises: 0001
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mysql

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def _normal_default(value) -> str:
    return (
        ("" if value is None else str(value)).lower().replace("()", "").replace(" ", "")
    )


def _ensure_table(name: str, *columns, **options) -> None:
    """MySQL DDL commits independently: only resume an exact partial schema."""
    if op.get_context().as_sql:
        op.create_table(name, *columns, **options)
        return
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if not inspector.has_table(name):
        op.create_table(name, *columns, **options)
        return
    expected = sa.Table(name, sa.MetaData(), *columns, **options)
    actual = inspector.get_columns(name)
    matches = len(actual) == len(expected.columns)
    for stored, wanted in zip(actual, expected.columns, strict=False):
        matches = matches and (
            stored["name"] == wanted.name
            and stored["type"].compile(dialect=bind.dialect)
            == wanted.type.compile(dialect=bind.dialect)
            and stored["nullable"] == wanted.nullable
            and _normal_default(stored.get("default"))
            == _normal_default(
                wanted.server_default.arg if wanted.server_default else None
            )
        )
        if wanted.primary_key:
            matches = matches and stored.get("autoincrement") is True
    matches = matches and inspector.get_pk_constraint(name)["constrained_columns"] == [
        c.name for c in expected.primary_key
    ]
    wanted_unique = {
        tuple(c.name for c in constraint.columns)
        for constraint in expected.constraints
        if isinstance(constraint, sa.UniqueConstraint)
    }
    stored_unique = {
        tuple(item["column_names"]) for item in inspector.get_unique_constraints(name)
    }
    matches = matches and wanted_unique == stored_unique
    wanted_fk = {
        (
            tuple(element.parent.name for element in constraint.elements),
            constraint.elements[0].target_fullname.split(".")[-2],
            tuple(
                element.target_fullname.split(".")[-1]
                for element in constraint.elements
            ),
            constraint.ondelete,
        )
        for constraint in expected.foreign_key_constraints
    }
    stored_fk = {
        (
            tuple(item["constrained_columns"]),
            item["referred_table"],
            tuple(item["referred_columns"]),
            item.get("options", {}).get("ondelete"),
        )
        for item in inspector.get_foreign_keys(name)
    }
    matches = matches and wanted_fk == stored_fk
    matches = (
        matches
        and inspector.get_table_options(name).get("mysql_engine", "").lower()
        == "innodb"
    )
    if not matches:
        raise RuntimeError(
            f"Existing {name} table differs from revision 0002; inspect the schema before proceeding"
        )


def upgrade() -> None:
    _ensure_table(
        "users",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "username",
            mysql.VARCHAR(128, charset="utf8mb4", collation="utf8mb4_bin"),
            nullable=False,
            unique=True,
        ),
        sa.Column("password_hash", sa.String(255), nullable=False),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.TIMESTAMP(),
            server_default=sa.text("CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        mysql_engine="InnoDB",
    )
    _ensure_table(
        "sessions",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "token_hash",
            mysql.CHAR(64, charset="ascii", collation="ascii_bin"),
            nullable=False,
            unique=True,
        ),
        sa.Column(
            "csrf_token",
            mysql.VARCHAR(64, charset="ascii", collation="ascii_bin"),
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        mysql_engine="InnoDB",
    )
    if op.get_context().as_sql:
        op.create_index("ix_sessions_expires_at", "sessions", ["expires_at"])
    else:
        indexes = sa.inspect(op.get_bind()).get_indexes("sessions")
        existing = next(
            (item for item in indexes if item["name"] == "ix_sessions_expires_at"), None
        )
        if existing is None:
            op.create_index("ix_sessions_expires_at", "sessions", ["expires_at"])
        elif existing["column_names"] != ["expires_at"] or existing["unique"]:
            raise RuntimeError(
                "Existing sessions expiry index differs from revision 0002"
            )


def downgrade() -> None:
    op.drop_table("sessions")
    op.drop_table("users")
