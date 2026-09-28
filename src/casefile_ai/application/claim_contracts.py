"""Atomic factual claims extracted from validated Grounded QA answers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, create_model, model_validator

from casefile_ai.application.contracts import AnswerCitation
from casefile_ai.application.qa_contracts import ModelSupportDecision
from casefile_ai.domain.ids import derived_id

ClaimKind = Literal["date", "amount", "action", "person", "other"]


class ClaimExtractionError(RuntimeError):
    """An answer or model output cannot be accepted as grounded claims."""


class ModelAtomicClaim(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    text: str = Field(min_length=1, max_length=2_000)
    kind: ClaimKind = Field(description=(
        "Core fact, not incidental words: amount=monetary attribute; date=date/time/term; "
        "person=identity, organization or role holder; action=act/status including negation; "
        "other=only facts outside these categories, such as a record-to-contract mapping."
    ))
    citation_ids: list[str] = Field(min_length=1, max_length=10)

    @model_validator(mode="after")
    def unique_citations(self) -> ModelAtomicClaim:
        if len(self.citation_ids) != len(set(self.citation_ids)):
            raise ValueError("citation_ids must be unique")
        return self


class ModelClaimExtraction(BaseModel):
    model_config = ConfigDict(extra="forbid")

    claims: list[ModelAtomicClaim] = Field(max_length=30)


class ModelClaimValidation(ModelSupportDecision):
    """Joint quality checks in the existing per-Claim evidence-support call."""

    model_config = ConfigDict(extra="forbid", strict=True, str_strip_whitespace=True)

    atomicity: Literal["matches", "mismatch", "uncertain"]
    scope_preservation: Literal["matches", "mismatch", "uncertain"]
    category_matches: Literal["matches", "mismatch", "uncertain"]


def extraction_model_for_aliases(aliases: tuple[str, ...]) -> type[ModelClaimExtraction]:
    if not aliases or len(aliases) != len(set(aliases)):
        raise ValueError("citation aliases must be nonempty and unique")
    alias_literal = Literal.__getitem__(aliases)
    claim_model = create_model(
        "AllowedAtomicClaim",
        __base__=ModelAtomicClaim,
        citation_ids=(list.__class_getitem__(alias_literal), Field(min_length=1, max_length=10)),
    )
    return create_model(
        "AllowedClaimExtraction",
        __base__=ModelClaimExtraction,
        claims=(list.__class_getitem__(claim_model), Field(max_length=30)),
    )


@dataclass(frozen=True, slots=True)
class ExtractedClaim:
    claim_id: str
    answer_id: str
    source_answer_claim_id: str
    text: str
    kind: ClaimKind
    evidence_ids: tuple[str, ...]
    extraction_version: str
    model_version: str


@dataclass(frozen=True, slots=True)
class CaseClaim:
    case_id: str
    claim: ExtractedClaim


@dataclass(frozen=True, slots=True)
class UncoveredFragment:
    source_answer_claim_id: str
    text: str
    start_offset: int
    end_offset: int


@dataclass(frozen=True, slots=True)
class ClaimCoverage:
    claim_id: str
    source_answer_claim_id: str
    evidence: tuple[AnswerCitation, ...]
    valid_evidence: bool
    issue: str | None


@dataclass(frozen=True, slots=True)
class SourceClaimCoverage:
    source_answer_claim_id: str
    source_text: str
    covered_claim_ids: tuple[str, ...]
    uncovered_fragments: tuple[UncoveredFragment, ...]


@dataclass(frozen=True, slots=True)
class ClaimCoverageResult:
    answer_id: str
    extraction_version: str
    model_version: str
    complete: bool
    sources: tuple[SourceClaimCoverage, ...]
    claims: tuple[ClaimCoverage, ...]


@dataclass(frozen=True, slots=True)
class ConflictCandidate:
    case_id: str
    left_claim: ExtractedClaim
    right_claim: ExtractedClaim
    left_evidence: tuple[AnswerCitation, ...]
    right_evidence: tuple[AnswerCitation, ...]
    shared_topic: str

    @property
    def candidate_id(self) -> str:
        return derived_id(
            "conflict-candidate-v1", self.case_id,
            self.left_claim.claim_id, self.right_claim.claim_id,
            self.left_claim.extraction_version, self.left_claim.model_version,
        )


class ClaimRepository(Protocol):
    def get_claims(
        self, answer_id: str, extraction_version: str, model_version: str
    ) -> tuple[ExtractedClaim, ...]: ...

    def save_claims(
        self, answer_id: str, claims: tuple[ExtractedClaim, ...]
    ) -> tuple[ExtractedClaim, ...]: ...

    def list_case_claims(
        self, case_id: str, extraction_version: str, model_version: str
    ) -> tuple[CaseClaim, ...]: ...
