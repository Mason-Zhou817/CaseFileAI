"""Persist versioned, candidate-bound contradiction decisions.

Revision ID: 20260923_0006
Revises: 20260923_0005
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260923_0006"
down_revision: str | None = "20260923_0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "contradiction_results",
        sa.Column("result_id", sa.String(36), nullable=False),
        sa.Column("case_id", sa.String(36), nullable=False),
        sa.Column("candidate_id", sa.String(36), nullable=False),
        sa.Column("left_claim_id", sa.String(36), nullable=False),
        sa.Column("right_claim_id", sa.String(36), nullable=False),
        sa.Column("contradiction_type", sa.String(16), nullable=False),
        sa.Column("verdict", sa.String(24), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("basis", sa.String(100), nullable=False),
        sa.Column("left_evidence_json", sa.Text(), nullable=False),
        sa.Column("right_evidence_json", sa.Text(), nullable=False),
        sa.Column("detector_version", sa.String(100), nullable=False),
        sa.Column("prompt_version", sa.String(100), nullable=False),
        sa.Column("model_version", sa.String(200), nullable=False),
        sa.Column("extraction_version", sa.String(100), nullable=False),
        sa.Column("claim_model_version", sa.String(200), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "contradiction_type IN ('date', 'amount', 'action', 'person')",
            name="ck_contradiction_results_valid_type",
        ),
        sa.CheckConstraint(
            "verdict IN ('contradiction', 'non_contradiction', 'uncertain')",
            name="ck_contradiction_results_valid_verdict",
        ),
        sa.CheckConstraint(
            "left_claim_id != right_claim_id", name="ck_contradiction_results_distinct_claims"
        ),
        sa.ForeignKeyConstraint(
            ["case_id"], ["cases.case_id"], name="fk_contradiction_results_case_id_cases"
        ),
        sa.ForeignKeyConstraint(
            ["left_claim_id"], ["claims.claim_id"],
            name="fk_contradiction_results_left_claim_id_claims",
        ),
        sa.ForeignKeyConstraint(
            ["right_claim_id"], ["claims.claim_id"],
            name="fk_contradiction_results_right_claim_id_claims",
        ),
        sa.PrimaryKeyConstraint("result_id", name="pk_contradiction_results"),
    )
    op.create_index("ix_contradiction_results_case_id", "contradiction_results", ["case_id"])
    op.create_index(
        "ix_contradiction_results_candidate_id", "contradiction_results", ["candidate_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_contradiction_results_candidate_id", table_name="contradiction_results")
    op.drop_index("ix_contradiction_results_case_id", table_name="contradiction_results")
    op.drop_table("contradiction_results")
