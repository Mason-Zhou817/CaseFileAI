"""Frozen Day 1 contracts shared by HTTP, CLI, and MCP adapters."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Protocol


@dataclass(frozen=True, slots=True)
class CaseSummary:
    case_id: str
    name: str
    description: str | None = None


@dataclass(frozen=True, slots=True)
class DocumentSummary:
    document_id: str
    case_id: str
    filename: str
    content_sha256: str
    page_count: int


@dataclass(frozen=True, slots=True)
class PageView:
    document_id: str
    page_id: str
    page_number: int
    filename: str
    text: str
    ocr_used: bool


@dataclass(frozen=True, slots=True)
class SearchHit:
    evidence_id: str
    chunk_id: str
    document_id: str
    document_version_id: str
    page_id: str
    filename: str
    page_number: int
    quote: str
    score: float
    rank: int
    bm25_score: float | None
    bm25_rank: int | None
    vector_score: float | None
    vector_rank: int | None
    retrieval_sources: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class AnswerCitation:
    evidence_id: str
    claim_id: str | None
    document_id: str
    page_id: str
    filename: str
    page_number: int
    quote: str
    document_version_id: str = ""


@dataclass(frozen=True, slots=True)
class AnswerClaim:
    claim_id: str
    text: str
    citation_ids: tuple[str, ...]
    confidence: float
    support_status: Literal["supported", "partially_supported", "unsupported"]


@dataclass(frozen=True, slots=True)
class QARunMetadata:
    retrieval_limit: int
    retrieval_version: str
    embedding_version: str
    chunking_version: str
    model_version: str
    prompt_version: str
    evidence_ids: tuple[str, ...]
    validation_status: str
    refusal_reason: str | None
    retrieval_ms: float
    generation_ms: float
    validation_ms: float
    total_ms: float
    cache_hit: bool = False


@dataclass(frozen=True, slots=True)
class GroundedAnswer:
    answer_id: str
    question: str
    answer: str
    citations: tuple[AnswerCitation, ...]
    abstained: bool
    claims: tuple[AnswerClaim, ...] = ()
    metadata: QARunMetadata | None = None


@dataclass(frozen=True, slots=True)
class ConflictView:
    conflict_id: str
    conflict_type: str
    severity: str
    explanation: str
    left_claim_id: str
    right_claim_id: str
    left_evidence: tuple[AnswerCitation, ...]
    right_evidence: tuple[AnswerCitation, ...]


class CaseService(Protocol):
    def create_case(self, name: str, description: str | None = None) -> CaseSummary: ...

    def list_cases(self) -> list[CaseSummary]: ...

    def get_case(self, case_id: str) -> CaseSummary: ...


class IngestionService(Protocol):
    def import_document(self, case_id: str, path: Path) -> DocumentSummary: ...

    def get_document_page(self, document_id: str, page_number: int) -> PageView: ...


class RetrievalService(Protocol):
    def search_case_evidence(
        self, case_id: str, query: str, limit: int = 10
    ) -> list[SearchHit]: ...


class QAService(Protocol):
    def ask_case(self, case_id: str, question: str, retrieval_limit: int = 5) -> GroundedAnswer: ...


class ConflictService(Protocol):
    def detect_conflicts(self, case_id: str) -> list[ConflictView]: ...
