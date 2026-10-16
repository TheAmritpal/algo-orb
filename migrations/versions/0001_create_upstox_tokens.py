"""Create the Upstox access token table.

Revision ID: 0001
Revises:
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "upstox_tokens",
        sa.Column("client_id", mysql.VARCHAR(255, charset="ascii", collation="ascii_bin"), nullable=False),
        sa.Column("user_id", mysql.VARCHAR(100, charset="ascii", collation="ascii_bin"), nullable=False),
        sa.Column("access_token", sa.Text(), nullable=False),
        sa.Column("created_at", sa.TIMESTAMP(), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.Column("updated_at", sa.TIMESTAMP(), server_default=sa.text("CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP"), nullable=False),
        sa.PrimaryKeyConstraint("client_id", "user_id"),
        mysql_engine="InnoDB",
    )


def downgrade() -> None:
    op.drop_table("upstox_tokens")
