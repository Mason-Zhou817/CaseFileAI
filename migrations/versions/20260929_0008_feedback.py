"""Persist local explicit answer feedback and human review."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260929_0008"
down_revision: str | None = "20260927_0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "feedback",
        sa.Column("feedback_id", sa.String(36), primary_key=True),
        sa.Column("case_id", sa.String(36), sa.ForeignKey("cases.case_id"), nullable=False),
        sa.Column("answer_id", sa.String(36), sa.ForeignKey("answers.answer_id"), nullable=False),
        sa.Column("rating", sa.String(32), nullable=False),
        sa.Column("category", sa.String(32), nullable=False),
        sa.Column("note", sa.Text(), nullable=False),
        sa.Column("review_status", sa.String(16), nullable=False),
        sa.Column("expected_status", sa.String(32)),
        sa.Column("expected_answer", sa.Text()),
        sa.Column("expected_evidence_json", sa.Text()),
        sa.Column("reviewed_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "rating IN ('correct', 'partially_correct', 'incorrect', 'insufficient_evidence')",
            name="ck_feedback_valid_rating",
        ),
        sa.CheckConstraint(
            "category IN ('retrieval', 'answer', 'citation', 'claim', 'other')",
            name="ck_feedback_valid_category",
        ),
        sa.CheckConstraint(
            "review_status IN ('pending', 'approved', 'rejected')",
            name="ck_feedback_valid_review_status",
        ),
    )
    for column in ("case_id", "answer_id", "created_at"):
        op.create_index(f"ix_feedback_{column}", "feedback", [column])


def downgrade() -> None:
    for column in ("created_at", "answer_id", "case_id"):
        op.drop_index(f"ix_feedback_{column}", table_name="feedback")
    op.drop_table("feedback")
