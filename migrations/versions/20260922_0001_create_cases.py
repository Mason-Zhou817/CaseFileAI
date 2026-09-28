"""Create the case aggregate root.

Revision ID: 20260922_0001
Revises: None
Create Date: 2026-09-22
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260922_0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "cases",
        sa.Column("case_id", sa.String(length=36), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="active"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("length(trim(name)) > 0", name="ck_cases_name_not_blank"),
        sa.PrimaryKeyConstraint("case_id", name="pk_cases"),
    )
    op.create_index("ix_cases_created_at", "cases", ["created_at"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_cases_created_at", table_name="cases")
    op.drop_table("cases")
