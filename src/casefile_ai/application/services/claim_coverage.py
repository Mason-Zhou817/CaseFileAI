"""Deterministic extractive coverage of a validated answer's factual QA claims."""

from __future__ import annotations

from typing import Protocol

from casefile_ai.application.claim_contracts import (
    ClaimCoverage,
    ClaimCoverageResult,
    ClaimExtractionError,
    ExtractedClaim,
    SourceClaimCoverage,
    UncoveredFragment,
)
from casefile_ai.application.contracts import GroundedAnswer
from casefile_ai.application.services.claim_provenance import (
    extractive_positions,
    visible_characters,
)


class AnswerLookup(Protocol):
    def get_answer(self, answer_id: str) -> GroundedAnswer | None: ...


class ClaimLookup(Protocol):
    def get_claims(
        self, answer_id: str, extraction_version: str, model_version: str
    ) -> tuple[ExtractedClaim, ...]: ...


def _visible_characters(value: str) -> tuple[str, list[int]]:
    return visible_characters(value)


class DefaultClaimCoverageService:
    def __init__(self, qa_repository: AnswerLookup, claim_repository: ClaimLookup) -> None:
        self._qa_repository = qa_repository
        self._claim_repository = claim_repository

    def evaluate(
        self, answer_id: str, extraction_version: str, model_version: str
    ) -> ClaimCoverageResult:
        answer = self._qa_repository.get_answer(answer_id)
        if answer is None or answer.abstained or answer.metadata is None:
            raise ClaimExtractionError("coverage requires an existing successful answer")
        if answer.metadata.validation_status != "validated":
            raise ClaimExtractionError("coverage requires a validated answer")
        if answer.answer != "\n".join(source.text for source in answer.claims):
            raise ClaimExtractionError("answer text does not match its validated QA claims")

        sources = {source.claim_id: source for source in answer.claims}
        extracted = self._claim_repository.get_claims(answer_id, extraction_version, model_version)
        coverage: list[ClaimCoverage] = []
        covered: dict[str, set[int]] = {source_id: set() for source_id in sources}
        mapped: dict[str, list[str]] = {source_id: [] for source_id in sources}
        for claim in extracted:
            source = sources.get(claim.source_answer_claim_id)
            citations = tuple(
                citation
                for citation in answer.citations
                if citation.claim_id == claim.source_answer_claim_id
                and citation.evidence_id in claim.evidence_ids
            )
            valid_evidence = bool(claim.evidence_ids) and source is not None and (
                len(citations) == len(claim.evidence_ids)
                and len(set(claim.evidence_ids)) == len(claim.evidence_ids)
                and set(claim.evidence_ids).issubset(set(source.citation_ids))
            )
            issue: str | None = None
            if claim.answer_id != answer_id or source is None:
                issue = "invalid_source_answer_claim"
            elif not valid_evidence:
                issue = "invalid_evidence"
            else:
                matched = extractive_positions(source.text, claim.text)
                if matched is None:
                    issue = "text_not_in_source_answer_claim"
                else:
                    _, positions = _visible_characters(source.text)
                    visible_indices = {position: index for index, position in enumerate(positions)}
                    covered[source.claim_id].update(
                        visible_indices[position] for position in matched
                    )
                    mapped[source.claim_id].append(claim.claim_id)
            coverage.append(
                ClaimCoverage(
                    claim_id=claim.claim_id,
                    source_answer_claim_id=claim.source_answer_claim_id,
                    evidence=citations,
                    valid_evidence=valid_evidence,
                    issue=issue,
                )
            )

        source_results: list[SourceClaimCoverage] = []
        for source in answer.claims:
            visible, positions = _visible_characters(source.text)
            missing = [
                index for index in range(len(visible)) if index not in covered[source.claim_id]
            ]
            fragments: list[UncoveredFragment] = []
            for index in missing:
                if fragments and positions[index] <= fragments[-1].end_offset + 1:
                    prior = fragments[-1]
                    end = positions[index] + 1
                    fragments[-1] = UncoveredFragment(
                        source.claim_id,
                        source.text[prior.start_offset:end],
                        prior.start_offset,
                        end,
                    )
                else:
                    start = positions[index]
                    fragments.append(
                        UncoveredFragment(
                            source.claim_id, source.text[start : start + 1], start, start + 1
                        )
                    )
            source_results.append(
                SourceClaimCoverage(
                    source_answer_claim_id=source.claim_id,
                    source_text=source.text,
                    covered_claim_ids=tuple(mapped[source.claim_id]),
                    uncovered_fragments=tuple(fragments),
                )
            )
        return ClaimCoverageResult(
            answer_id=answer_id,
            extraction_version=extraction_version,
            model_version=model_version,
            complete=bool(source_results)
            and all(not item.uncovered_fragments for item in source_results)
            and all(item.issue is None for item in coverage),
            sources=tuple(source_results),
            claims=tuple(coverage),
        )
