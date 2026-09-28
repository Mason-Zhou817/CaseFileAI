"""Persist evidence, grounded answers and immutable citation lineage.

Revision ID: 20260923_0004
Revises: 20260923_0003
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260923_0004"
down_revision: str | None = "20260923_0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "evidence",
        sa.Column("evidence_id", sa.String(36), nullable=False),
        sa.Column("case_id", sa.String(36), nullable=False),
        sa.Column("chunk_id", sa.String(36), nullable=False),
        sa.Column("quote", sa.Text(), nullable=False),
        sa.Column("start_offset", sa.Integer(), nullable=False),
        sa.Column("end_offset", sa.Integer(), nullable=False),
        sa.Column("evidence_type", sa.String(32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "start_offset >= 0", name="ck_evidence_nonnegative_start_offset"
        ),
        sa.CheckConstraint("end_offset > start_offset", name="ck_evidence_valid_offsets"),
        sa.ForeignKeyConstraint(["case_id"], ["cases.case_id"], name="fk_evidence_case_id_cases"),
        sa.ForeignKeyConstraint(
            ["chunk_id"], ["chunks.chunk_id"], name="fk_evidence_chunk_id_chunks"
        ),
        sa.PrimaryKeyConstraint("evidence_id", name="pk_evidence"),
        sa.UniqueConstraint("chunk_id", "evidence_type", name="uq_evidence_chunk_type"),
    )
    op.create_index("ix_evidence_case_id", "evidence", ["case_id"])
    op.create_index("ix_evidence_chunk_id", "evidence", ["chunk_id"])

    op.create_table(
        "questions",
        sa.Column("question_id", sa.String(36), nullable=False),
        sa.Column("case_id", sa.String(36), nullable=False),
        sa.Column("question", sa.Text(), nullable=False),
        sa.Column("normalized_question", sa.Text(), nullable=False),
        sa.Column("retrieval_limit", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "length(trim(question)) > 0", name="ck_questions_question_not_blank"
        ),
        sa.CheckConstraint(
            "retrieval_limit > 0", name="ck_questions_positive_retrieval_limit"
        ),
        sa.ForeignKeyConstraint(["case_id"], ["cases.case_id"], name="fk_questions_case_id_cases"),
        sa.PrimaryKeyConstraint("question_id", name="pk_questions"),
    )
    op.create_index("ix_questions_case_id", "questions", ["case_id"])
    op.create_index("ix_questions_created_at", "questions", ["created_at"])

    op.create_table(
        "answers",
        sa.Column("answer_id", sa.String(36), nullable=False),
        sa.Column("question_id", sa.String(36), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("answer", sa.Text(), nullable=False),
        sa.Column("claims_json", sa.Text(), nullable=False),
        sa.Column("evidence_ids_json", sa.Text(), nullable=False),
        sa.Column("model_version", sa.String(200), nullable=False),
        sa.Column("prompt_version", sa.String(100), nullable=False),
        sa.Column("retrieval_version", sa.String(100), nullable=False),
        sa.Column("embedding_version", sa.String(200), nullable=False),
        sa.Column("chunking_version", sa.String(100), nullable=False),
        sa.Column("source_fingerprint", sa.String(64), nullable=False),
        sa.Column("index_fingerprint", sa.String(64), nullable=False),
        sa.Column("cache_key", sa.String(64), nullable=False),
        sa.Column("validation_status", sa.String(64), nullable=False),
        sa.Column("refusal_reason", sa.String(64), nullable=True),
        sa.Column("retrieval_ms", sa.Float(), nullable=False),
        sa.Column("generation_ms", sa.Float(), nullable=False),
        sa.Column("validation_ms", sa.Float(), nullable=False),
        sa.Column("total_ms", sa.Float(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "status IN ('answered', 'insufficient_evidence')", name="ck_answers_valid_status"
        ),
        sa.ForeignKeyConstraint(
            ["question_id"], ["questions.question_id"], name="fk_answers_question_id_questions"
        ),
        sa.PrimaryKeyConstraint("answer_id", name="pk_answers"),
        sa.UniqueConstraint("question_id", name="uq_answers_question_id"),
    )
    op.create_index("ix_answers_question_id", "answers", ["question_id"])
    op.create_index("ix_answers_cache_key", "answers", ["cache_key"], unique=True)
    op.create_index("ix_answers_created_at", "answers", ["created_at"])

    op.create_table(
        "answer_citations",
        sa.Column("answer_citation_id", sa.String(36), nullable=False),
        sa.Column("answer_id", sa.String(36), nullable=False),
        sa.Column("evidence_id", sa.String(36), nullable=False),
        sa.Column("claim_id", sa.String(36), nullable=False),
        sa.Column("citation_order", sa.Integer(), nullable=False),
        sa.CheckConstraint(
            "citation_order >= 0", name="ck_answer_citations_nonnegative_citation_order"
        ),
        sa.ForeignKeyConstraint(
            ["answer_id"], ["answers.answer_id"], name="fk_answer_citations_answer_id_answers"
        ),
        sa.ForeignKeyConstraint(
            ["evidence_id"],
            ["evidence.evidence_id"],
            name="fk_answer_citations_evidence_id_evidence",
        ),
        sa.PrimaryKeyConstraint("answer_citation_id", name="pk_answer_citations"),
        sa.UniqueConstraint(
            "answer_id", "claim_id", "evidence_id", name="uq_answer_claim_evidence"
        ),
    )
    op.create_index("ix_answer_citations_answer_id", "answer_citations", ["answer_id"])
    op.create_index("ix_answer_citations_evidence_id", "answer_citations", ["evidence_id"])


def downgrade() -> None:
    op.drop_index("ix_answer_citations_evidence_id", table_name="answer_citations")
    op.drop_index("ix_answer_citations_answer_id", table_name="answer_citations")
    op.drop_table("answer_citations")
    op.drop_index("ix_answers_created_at", table_name="answers")
    op.drop_index("ix_answers_cache_key", table_name="answers")
    op.drop_index("ix_answers_question_id", table_name="answers")
    op.drop_table("answers")
    op.drop_index("ix_questions_created_at", table_name="questions")
    op.drop_index("ix_questions_case_id", table_name="questions")
    op.drop_table("questions")
    op.drop_index("ix_evidence_chunk_id", table_name="evidence")
    op.drop_index("ix_evidence_case_id", table_name="evidence")
    op.drop_table("evidence")
