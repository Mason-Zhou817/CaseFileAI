"""Store version-scoped index outcomes without document text or error bodies."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260927_0007"
down_revision: str | None = "20260923_0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "material_index_attempts",
        sa.Column("document_version_id", sa.String(36), nullable=False),
        sa.Column("signature", sa.String(64), nullable=False),
        sa.Column("outcome", sa.String(16), nullable=False),
        sa.Column("diagnostic_id", sa.String(36), nullable=True),
        sa.ForeignKeyConstraint(["document_version_id"], ["document_versions.document_version_id"]),
        sa.PrimaryKeyConstraint("document_version_id", "signature"),
        sa.CheckConstraint("outcome IN ('ready', 'failed')", name="safe_index_outcome"),
    )


def downgrade() -> None:
    op.drop_table("material_index_attempts")
