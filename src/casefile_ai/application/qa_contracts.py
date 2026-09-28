"""Contracts for persisted, source-traceable question answering."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol, TypeVar

from pydantic import BaseModel, ConfigDict, Field, create_model, model_validator

from casefile_ai.application.contracts import GroundedAnswer, SearchHit

QAStatus = Literal["answered", "insufficient_evidence"]
SupportStatus = Literal["supported", "partially_supported", "unsupported"]


class InvalidQuestionError(ValueError):
    pass


class CitationValidationError(RuntimeError):
    pass


class QAUnavailableError(RuntimeError):
    pass


class ModelAnswerClaim(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    text: str = Field(min_length=1, max_length=2_000)
    citation_ids: list[str] = Field(min_length=1, max_length=10)
    confidence: float = Field(ge=0.0, le=1.0)

    @model_validator(mode="after")
    def citations_are_unique(self) -> ModelAnswerClaim:
        if len(set(self.citation_ids)) != len(self.citation_ids):
            raise ValueError("citation_ids must be unique")
        return self


class ModelGroundedAnswer(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    status: QAStatus
    answer: str = Field(max_length=10_000)
    claims: list[ModelAnswerClaim] = Field(max_length=30)

    @model_validator(mode="after")
    def status_matches_claims(self) -> ModelGroundedAnswer:
        if self.status == "answered" and (not self.answer or not self.claims):
            raise ValueError("answered output requires an answer and at least one claim")
        if self.status == "insufficient_evidence" and self.claims:
            raise ValueError("insufficient_evidence output must not contain claims")
        return self


def answer_model_for_aliases(aliases: tuple[str, ...]) -> type[ModelGroundedAnswer]:
    """Build a strict per-round schema whose citations are offered aliases only."""

    if not aliases or len(aliases) != len(set(aliases)):
        raise ValueError("citation aliases must be nonempty and unique")
    alias_literal = Literal.__getitem__(aliases)
    claim_model = create_model(
        "AllowedAnswerClaim",
        __base__=ModelAnswerClaim,
        citation_ids=(list.__class_getitem__(alias_literal), Field(min_length=1, max_length=10)),
    )
    answer_model = create_model(
        "AllowedGroundedAnswer",
        __base__=ModelGroundedAnswer,
        claims=(list.__class_getitem__(claim_model), Field(max_length=30)),
    )
    return answer_model


class ModelSupportDecision(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    verdict: SupportStatus
    reason: str = Field(min_length=1, max_length=1_000)


class ModelAnswerAlignment(BaseModel):
    """Question alignment is independent of literal citation support.

    Every dimension must explicitly match; uncertainty never grants validation.
    """

    model_config = ConfigDict(extra="forbid", strict=True, str_strip_whitespace=True)

    requested_fact: str = Field(min_length=1, max_length=1_000)
    requested_scope: str = Field(min_length=1, max_length=1_000)
    answers_requested_fact: Literal["matches", "mismatch", "uncertain"]
    field_matches: Literal["matches", "mismatch", "uncertain"]
    source_scope_matches: Literal["matches", "mismatch", "uncertain"]
    reason: str = Field(min_length=1, max_length=1_000)


ModelResponse = TypeVar("ModelResponse", bound=BaseModel)


class StructuredLLMProvider(Protocol):
    @property
    def model_version(self) -> str: ...

    def generate_structured(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        response_model: type[ModelResponse],
    ) -> ModelResponse: ...


class GroundedRetrievalService(Protocol):
    @property
    def embedding_version(self) -> str: ...

    @property
    def retrieval_version(self) -> str: ...

    def search_case_evidence(
        self, case_id: str, query: str, limit: int = 10
    ) -> list[SearchHit]: ...


@dataclass(frozen=True, slots=True)
class EvidenceRecord:
    evidence_id: str
    case_id: str
    chunk_id: str
    document_id: str
    document_version_id: str
    page_id: str
    filename: str
    page_number: int
    quote: str
    start_offset: int
    end_offset: int
    evidence_type: str


@dataclass(frozen=True, slots=True)
class CaseIndexState:
    source_fingerprint: str
    index_fingerprint: str
    complete: bool


@dataclass(frozen=True, slots=True)
class PersistAnswerDraft:
    answer_id: str
    question_id: str
    case_id: str
    question: str
    normalized_question: str
    retrieval_limit: int
    status: QAStatus
    answer: str
    claims_json: str
    evidence_ids_json: str
    citations: tuple[tuple[str, str], ...]
    model_version: str
    prompt_version: str
    retrieval_version: str
    embedding_version: str
    chunking_version: str
    source_fingerprint: str
    index_fingerprint: str
    cache_key: str
    validation_status: str
    refusal_reason: str | None
    retrieval_ms: float
    generation_ms: float
    validation_ms: float
    total_ms: float


class QARepository(Protocol):
    def index_state(
        self, case_id: str, chunking_version: str, embedding_version: str
    ) -> CaseIndexState: ...

    def materialize_evidence(self, case_id: str, hits: list[SearchHit]) -> list[EvidenceRecord]: ...

    def resolve_evidence(
        self, case_id: str, evidence_ids: tuple[str, ...]
    ) -> dict[str, EvidenceRecord]: ...

    def get_cached_answer(self, cache_key: str) -> GroundedAnswer | None: ...

    def save_answer(self, draft: PersistAnswerDraft) -> GroundedAnswer: ...

    def get_answer(self, answer_id: str) -> GroundedAnswer | None: ...
