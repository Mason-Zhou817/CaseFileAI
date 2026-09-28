"""Immutable document versions and page provenance."""

import sqlalchemy as sa
from alembic import op

revision = "20260923_0002"
down_revision = "20260922_0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "documents",
        sa.Column("document_id", sa.String(36), primary_key=True),
        sa.Column("case_id", sa.String(36), sa.ForeignKey("cases.case_id"), nullable=False),
    )
    op.create_index("ix_documents_case_id", "documents", ["case_id"])
    op.create_table(
        "document_versions",
        sa.Column("document_version_id", sa.String(36), primary_key=True),
        sa.Column(
            "document_id", sa.String(36), sa.ForeignKey("documents.document_id"), nullable=False
        ),
        sa.Column("version_number", sa.Integer(), nullable=False),
        sa.Column("filename", sa.Text(), nullable=False),
        sa.Column("content_sha256", sa.String(64), nullable=False),
        sa.Column("storage_key", sa.String(80), nullable=False, unique=True),
        sa.Column("page_count", sa.Integer(), nullable=False),
        sa.Column("parser_version", sa.String(100), nullable=False),
        sa.UniqueConstraint("document_id", "content_sha256", name="uq_document_version_content"),
        sa.UniqueConstraint("document_id", "version_number", name="uq_document_version_number"),
        sa.CheckConstraint("version_number > 0", name="positive_version"),
        sa.CheckConstraint("page_count > 0", name="positive_page_count"),
    )
    op.create_index("ix_document_versions_document_id", "document_versions", ["document_id"])
    op.create_index("ix_document_versions_content_sha256", "document_versions", ["content_sha256"])
    op.create_table(
        "pages",
        sa.Column("page_id", sa.String(36), primary_key=True),
        sa.Column(
            "document_version_id",
            sa.String(36),
            sa.ForeignKey("document_versions.document_version_id"),
            nullable=False,
        ),
        sa.Column("page_number", sa.Integer(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("text_hash", sa.String(64), nullable=False),
        sa.Column("ocr_used", sa.Boolean(), nullable=False),
        sa.UniqueConstraint("document_version_id", "page_number"),
        sa.CheckConstraint("page_number > 0", name="positive_page_number"),
    )
    op.create_index("ix_pages_document_version_id", "pages", ["document_version_id"])


def downgrade() -> None:
    op.drop_table("pages")
    op.drop_table("document_versions")
    op.drop_table("documents")
