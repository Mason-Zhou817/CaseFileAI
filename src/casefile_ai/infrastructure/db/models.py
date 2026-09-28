"""SQLAlchemy models introduced incrementally through Alembic migrations."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    LargeBinary,
    MetaData,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


def utc_now() -> datetime:
    return datetime.now(UTC)


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


class CaseModel(Base):
    __tablename__ = "cases"
    __table_args__ = (CheckConstraint("length(trim(name)) > 0", name="name_not_blank"),)

    case_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="active")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now, index=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now
    )


class DocumentModel(Base):
    __tablename__ = "documents"
    document_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    case_id: Mapped[str] = mapped_column(ForeignKey("cases.case_id"), nullable=False, index=True)


class DocumentVersionModel(Base):
    __tablename__ = "document_versions"
    __table_args__ = (
        UniqueConstraint("document_id", "content_sha256", name="uq_document_version_content"),
        UniqueConstraint("document_id", "version_number", name="uq_document_version_number"),
        CheckConstraint("version_number > 0", name="positive_version"),
        CheckConstraint("page_count > 0", name="positive_page_count"),
    )
    document_version_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    document_id: Mapped[str] = mapped_column(
        ForeignKey("documents.document_id"), nullable=False, index=True
    )
    version_number: Mapped[int] = mapped_column(Integer, nullable=False)
    filename: Mapped[str] = mapped_column(Text, nullable=False)
    content_sha256: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    storage_key: Mapped[str] = mapped_column(String(80), nullable=False, unique=True)
    page_count: Mapped[int] = mapped_column(Integer, nullable=False)
    parser_version: Mapped[str] = mapped_column(String(100), nullable=False)


class MaterialIndexAttemptModel(Base):
    __tablename__ = "material_index_attempts"
    __table_args__ = (
        CheckConstraint("outcome IN ('ready', 'failed')", name="safe_index_outcome"),
    )
    document_version_id: Mapped[str] = mapped_column(
        ForeignKey("document_versions.document_version_id"), primary_key=True
    )
    signature: Mapped[str] = mapped_column(String(64), primary_key=True)
    outcome: Mapped[str] = mapped_column(String(16), nullable=False)
    diagnostic_id: Mapped[str | None] = mapped_column(String(36), nullable=True)


class PageModel(Base):
    __tablename__ = "pages"
    __table_args__ = (
        UniqueConstraint("document_version_id", "page_number"),
        CheckConstraint("page_number > 0", name="positive_page_number"),
    )
    page_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    document_version_id: Mapped[str] = mapped_column(
        ForeignKey("document_versions.document_version_id"), nullable=False, index=True
    )
    page_number: Mapped[int] = mapped_column(Integer, nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    text_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    ocr_used: Mapped[bool] = mapped_column(Boolean, nullable=False)


class ChunkModel(Base):
    __tablename__ = "chunks"
    __table_args__ = (
        UniqueConstraint(
            "page_id", "chunking_version", "chunk_index", name="uq_chunk_page_version_index"
        ),
        CheckConstraint("chunk_index >= 0", name="nonnegative_chunk_index"),
        CheckConstraint("start_offset >= 0", name="nonnegative_start_offset"),
        CheckConstraint("end_offset > start_offset", name="valid_offsets"),
    )

    chunk_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    case_id: Mapped[str] = mapped_column(ForeignKey("cases.case_id"), nullable=False, index=True)
    page_id: Mapped[str] = mapped_column(ForeignKey("pages.page_id"), nullable=False, index=True)
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    text_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    start_offset: Mapped[int] = mapped_column(Integer, nullable=False)
    end_offset: Mapped[int] = mapped_column(Integer, nullable=False)
    chunking_version: Mapped[str] = mapped_column(String(100), nullable=False, index=True)


class ChunkEmbeddingModel(Base):
    __tablename__ = "chunk_embeddings"

    chunk_id: Mapped[str] = mapped_column(
        ForeignKey("chunks.chunk_id"), primary_key=True, nullable=False
    )
    embedding_version: Mapped[str] = mapped_column(String(200), primary_key=True)
    dimensions: Mapped[int] = mapped_column(Integer, nullable=False)
    vector: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)


class EvidenceModel(Base):
    __tablename__ = "evidence"
    __table_args__ = (
        UniqueConstraint("chunk_id", "evidence_type", name="uq_evidence_chunk_type"),
        CheckConstraint("start_offset >= 0", name="nonnegative_start_offset"),
        CheckConstraint("end_offset > start_offset", name="valid_offsets"),
    )

    evidence_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    case_id: Mapped[str] = mapped_column(ForeignKey("cases.case_id"), nullable=False, index=True)
    chunk_id: Mapped[str] = mapped_column(
        ForeignKey("chunks.chunk_id"), nullable=False, index=True
    )
    quote: Mapped[str] = mapped_column(Text, nullable=False)
    start_offset: Mapped[int] = mapped_column(Integer, nullable=False)
    end_offset: Mapped[int] = mapped_column(Integer, nullable=False)
    evidence_type: Mapped[str] = mapped_column(String(32), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )


class QuestionModel(Base):
    __tablename__ = "questions"
    __table_args__ = (
        CheckConstraint("length(trim(question)) > 0", name="question_not_blank"),
        CheckConstraint("retrieval_limit > 0", name="positive_retrieval_limit"),
    )

    question_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    case_id: Mapped[str] = mapped_column(ForeignKey("cases.case_id"), nullable=False, index=True)
    question: Mapped[str] = mapped_column(Text, nullable=False)
    normalized_question: Mapped[str] = mapped_column(Text, nullable=False)
    retrieval_limit: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now, index=True
    )


class AnswerModel(Base):
    __tablename__ = "answers"
    __table_args__ = (
        UniqueConstraint("question_id", name="uq_answers_question_id"),
        CheckConstraint(
            "status IN ('answered', 'insufficient_evidence')", name="valid_status"
        ),
    )

    answer_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    question_id: Mapped[str] = mapped_column(
        ForeignKey("questions.question_id"), nullable=False, index=True
    )
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    answer: Mapped[str] = mapped_column(Text, nullable=False)
    claims_json: Mapped[str] = mapped_column(Text, nullable=False)
    evidence_ids_json: Mapped[str] = mapped_column(Text, nullable=False)
    model_version: Mapped[str] = mapped_column(String(200), nullable=False)
    prompt_version: Mapped[str] = mapped_column(String(100), nullable=False)
    retrieval_version: Mapped[str] = mapped_column(String(100), nullable=False)
    embedding_version: Mapped[str] = mapped_column(String(200), nullable=False)
    chunking_version: Mapped[str] = mapped_column(String(100), nullable=False)
    source_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    index_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    cache_key: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)
    validation_status: Mapped[str] = mapped_column(String(64), nullable=False)
    refusal_reason: Mapped[str | None] = mapped_column(String(64), nullable=True)
    retrieval_ms: Mapped[float] = mapped_column(Float, nullable=False)
    generation_ms: Mapped[float] = mapped_column(Float, nullable=False)
    validation_ms: Mapped[float] = mapped_column(Float, nullable=False)
    total_ms: Mapped[float] = mapped_column(Float, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now, index=True
    )


class AnswerCitationModel(Base):
    __tablename__ = "answer_citations"
    __table_args__ = (
        UniqueConstraint(
            "answer_id", "claim_id", "evidence_id", name="uq_answer_claim_evidence"
        ),
        CheckConstraint("citation_order >= 0", name="nonnegative_citation_order"),
    )

    answer_citation_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    answer_id: Mapped[str] = mapped_column(
        ForeignKey("answers.answer_id"), nullable=False, index=True
    )
    evidence_id: Mapped[str] = mapped_column(
        ForeignKey("evidence.evidence_id"), nullable=False, index=True
    )
    claim_id: Mapped[str] = mapped_column(String(36), nullable=False)
    citation_order: Mapped[int] = mapped_column(Integer, nullable=False)


class ClaimModel(Base):
    __tablename__ = "claims"
    __table_args__ = (
        CheckConstraint("length(trim(text)) > 0", name="text_not_blank"),
        CheckConstraint(
            "kind IN ('date', 'amount', 'action', 'person', 'other')", name="valid_kind"
        ),
    )

    claim_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    answer_id: Mapped[str] = mapped_column(
        ForeignKey("answers.answer_id"), nullable=False, index=True
    )
    source_answer_claim_id: Mapped[str] = mapped_column(String(36), nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    extraction_version: Mapped[str] = mapped_column(String(100), nullable=False)
    model_version: Mapped[str] = mapped_column(String(200), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )


class ClaimEvidenceModel(Base):
    __tablename__ = "claim_evidence"
    __table_args__ = (
        UniqueConstraint("claim_id", "answer_citation_id", name="uq_claim_answer_citation"),
    )

    claim_evidence_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    claim_id: Mapped[str] = mapped_column(ForeignKey("claims.claim_id"), nullable=False, index=True)
    answer_citation_id: Mapped[str] = mapped_column(
        ForeignKey("answer_citations.answer_citation_id"), nullable=False, index=True
    )


class ContradictionResultModel(Base):
    __tablename__ = "contradiction_results"
    __table_args__ = (
        CheckConstraint(
            "contradiction_type IN ('date', 'amount', 'action', 'person')",
            name="valid_type",
        ),
        CheckConstraint(
            "verdict IN ('contradiction', 'non_contradiction', 'uncertain')",
            name="valid_verdict",
        ),
        CheckConstraint("left_claim_id != right_claim_id", name="distinct_claims"),
    )

    result_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    case_id: Mapped[str] = mapped_column(ForeignKey("cases.case_id"), nullable=False, index=True)
    candidate_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    left_claim_id: Mapped[str] = mapped_column(ForeignKey("claims.claim_id"), nullable=False)
    right_claim_id: Mapped[str] = mapped_column(ForeignKey("claims.claim_id"), nullable=False)
    contradiction_type: Mapped[str] = mapped_column(String(16), nullable=False)
    verdict: Mapped[str] = mapped_column(String(24), nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    basis: Mapped[str] = mapped_column(String(100), nullable=False)
    left_evidence_json: Mapped[str] = mapped_column(Text, nullable=False)
    right_evidence_json: Mapped[str] = mapped_column(Text, nullable=False)
    detector_version: Mapped[str] = mapped_column(String(100), nullable=False)
    prompt_version: Mapped[str] = mapped_column(String(100), nullable=False)
    model_version: Mapped[str] = mapped_column(String(200), nullable=False)
    extraction_version: Mapped[str] = mapped_column(String(100), nullable=False)
    claim_model_version: Mapped[str] = mapped_column(String(200), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )
