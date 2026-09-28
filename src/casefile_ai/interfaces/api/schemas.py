"""HTTP request and response schemas."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class CreateCaseRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    name: str = Field(min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=5_000)


class CaseResponse(BaseModel):
    case_id: str
    name: str
    description: str | None = None
    status: Literal["active", "archived"] = "active"


class RenameCaseRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    name: str = Field(min_length=1, max_length=200)


class ArchiveCaseRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    archived: bool


class CaseListResponse(BaseModel):
    cases: list[CaseResponse]


class ErrorResponse(BaseModel):
    detail: str


class AskCaseRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    question: str = Field(min_length=1, max_length=1_000)
    retrieval_limit: int = Field(default=5, ge=1, le=20)


class AnswerClaimResponse(BaseModel):
    claim_id: str
    text: str
    citation_ids: list[str]
    confidence: float
    support_status: Literal["supported", "partially_supported", "unsupported"]


class AnswerCitationResponse(BaseModel):
    evidence_id: str
    claim_id: str | None
    document_id: str
    document_version_id: str
    page_id: str
    filename: str
    page_number: int
    quote: str


class QARunMetadataResponse(BaseModel):
    retrieval_limit: int
    retrieval_version: str
    embedding_version: str
    chunking_version: str
    model_version: str
    prompt_version: str
    evidence_ids: list[str]
    validation_status: str
    refusal_reason: str | None
    retrieval_ms: float
    generation_ms: float
    validation_ms: float
    total_ms: float
    cache_hit: bool


class AskCaseResponse(BaseModel):
    answer_id: str
    status: Literal["answered", "insufficient_evidence"]
    question: str
    answer: str
    claims: list[AnswerClaimResponse]
    citations: list[AnswerCitationResponse]
    metadata: QARunMetadataResponse
