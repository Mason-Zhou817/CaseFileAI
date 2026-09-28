"""Persist atomic claims and their validated answer-citation links.

Revision ID: 20260923_0005
Revises: 20260923_0004
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260923_0005"
down_revision: str | None = "20260923_0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "claims",
        sa.Column("claim_id", sa.String(36), nullable=False),
        sa.Column("answer_id", sa.String(36), nullable=False),
        sa.Column("source_answer_claim_id", sa.String(36), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("extraction_version", sa.String(100), nullable=False),
        sa.Column("model_version", sa.String(200), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("length(trim(text)) > 0", name="ck_claims_text_not_blank"),
        sa.CheckConstraint(
            "kind IN ('date', 'amount', 'action', 'person', 'other')", name="ck_claims_valid_kind"
        ),
        sa.ForeignKeyConstraint(
            ["answer_id"], ["answers.answer_id"], name="fk_claims_answer_id_answers"
        ),
        sa.PrimaryKeyConstraint("claim_id", name="pk_claims"),
    )
    op.create_index("ix_claims_answer_id", "claims", ["answer_id"])
    op.create_table(
        "claim_evidence",
        sa.Column("claim_evidence_id", sa.String(36), nullable=False),
        sa.Column("claim_id", sa.String(36), nullable=False),
        sa.Column("answer_citation_id", sa.String(36), nullable=False),
        sa.ForeignKeyConstraint(
            ["claim_id"], ["claims.claim_id"], name="fk_claim_evidence_claim_id_claims"
        ),
        sa.ForeignKeyConstraint(
            ["answer_citation_id"],
            ["answer_citations.answer_citation_id"],
            name="fk_claim_evidence_answer_citation_id_answer_citations",
        ),
        sa.PrimaryKeyConstraint("claim_evidence_id", name="pk_claim_evidence"),
        sa.UniqueConstraint("claim_id", "answer_citation_id", name="uq_claim_answer_citation"),
    )
    op.create_index("ix_claim_evidence_claim_id", "claim_evidence", ["claim_id"])
    op.create_index(
        "ix_claim_evidence_answer_citation_id", "claim_evidence", ["answer_citation_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_claim_evidence_answer_citation_id", table_name="claim_evidence")
    op.drop_index("ix_claim_evidence_claim_id", table_name="claim_evidence")
    op.drop_table("claim_evidence")
    op.drop_index("ix_claims_answer_id", table_name="claims")
    op.drop_table("claims")
