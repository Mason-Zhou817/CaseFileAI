"""Page-local chunks, vectors and Chinese FTS5 index.

Revision ID: 20260923_0003
Revises: 20260923_0002
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260923_0003"
down_revision: str | None = "20260923_0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "chunks",
        sa.Column("chunk_id", sa.String(36), nullable=False),
        sa.Column("case_id", sa.String(36), nullable=False),
        sa.Column("page_id", sa.String(36), nullable=False),
        sa.Column("chunk_index", sa.Integer(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("text_hash", sa.String(64), nullable=False),
        sa.Column("start_offset", sa.Integer(), nullable=False),
        sa.Column("end_offset", sa.Integer(), nullable=False),
        sa.Column("chunking_version", sa.String(100), nullable=False),
        sa.CheckConstraint("chunk_index >= 0", name="ck_chunks_nonnegative_chunk_index"),
        sa.CheckConstraint("start_offset >= 0", name="ck_chunks_nonnegative_start_offset"),
        sa.CheckConstraint("end_offset > start_offset", name="ck_chunks_valid_offsets"),
        sa.ForeignKeyConstraint(["case_id"], ["cases.case_id"], name="fk_chunks_case_id_cases"),
        sa.ForeignKeyConstraint(["page_id"], ["pages.page_id"], name="fk_chunks_page_id_pages"),
        sa.PrimaryKeyConstraint("chunk_id", name="pk_chunks"),
        sa.UniqueConstraint(
            "page_id", "chunking_version", "chunk_index", name="uq_chunk_page_version_index"
        ),
    )
    op.create_index("ix_chunks_case_id", "chunks", ["case_id"], unique=False)
    op.create_index("ix_chunks_page_id", "chunks", ["page_id"], unique=False)
    op.create_index("ix_chunks_chunking_version", "chunks", ["chunking_version"], unique=False)
    op.create_table(
        "chunk_embeddings",
        sa.Column("chunk_id", sa.String(36), nullable=False),
        sa.Column("embedding_version", sa.String(200), nullable=False),
        sa.Column("dimensions", sa.Integer(), nullable=False),
        sa.Column("vector", sa.LargeBinary(), nullable=False),
        sa.ForeignKeyConstraint(
            ["chunk_id"], ["chunks.chunk_id"], name="fk_chunk_embeddings_chunk_id_chunks"
        ),
        sa.PrimaryKeyConstraint("chunk_id", "embedding_version", name="pk_chunk_embeddings"),
    )
    op.execute(
        "CREATE VIRTUAL TABLE chunk_fts USING fts5("
        "chunk_id UNINDEXED, case_id UNINDEXED, tokenization_version UNINDEXED, "
        "search_text, tokenize='unicode61')"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS chunk_fts")
    op.drop_table("chunk_embeddings")
    op.drop_index("ix_chunks_chunking_version", table_name="chunks")
    op.drop_index("ix_chunks_page_id", table_name="chunks")
    op.drop_index("ix_chunks_case_id", table_name="chunks")
    op.drop_table("chunks")
