"""Versioned contradiction decisions over previously validated candidates."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field

from casefile_ai.application.claim_contracts import ClaimKind
from casefile_ai.application.contracts import AnswerCitation

Verdict = Literal["contradiction", "non_contradiction", "uncertain"]
Occurrence = Literal["occurred", "did_not_occur", "unknown"]


class ContradictionValidationError(ValueError):
    """A candidate or model decision cannot be accepted as grounded."""


class ModelSemanticDecision(BaseModel):
    """No evidence or claim IDs are model writable; the backend binds all provenance."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    verdict: Verdict
    same_event: bool
    same_fact_slot: bool
    reason: str = Field(min_length=1, max_length=1_000)
    left_occurrence: Occurrence
    right_occurrence: Occurrence
    left_identity: str = Field(max_length=200)
    right_identity: str = Field(max_length=200)


@dataclass(frozen=True, slots=True)
class ContradictionResult:
    result_id: str
    case_id: str
    candidate_id: str
    left_claim_id: str
    right_claim_id: str
    contradiction_type: ClaimKind
    verdict: Verdict
    reason: str
    basis: str
    left_evidence: tuple[AnswerCitation, ...]
    right_evidence: tuple[AnswerCitation, ...]
    detector_version: str
    prompt_version: str
    model_version: str
    extraction_version: str
    claim_model_version: str


class ContradictionRepository(Protocol):
    def get_result(self, result_id: str) -> ContradictionResult | None: ...

    def save_result(self, result: ContradictionResult) -> ContradictionResult: ...

    def list_case_results(self, case_id: str) -> tuple[ContradictionResult, ...]: ...
