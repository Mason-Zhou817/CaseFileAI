"""Case-scoped orchestration for the browser and other thin adapters."""

from __future__ import annotations

from typing import Protocol

from casefile_ai.application.claim_contracts import (
    ClaimCoverageResult,
    ClaimRepository,
    ConflictCandidate,
    ExtractedClaim,
)
from casefile_ai.application.contracts import CaseService, GroundedAnswer
from casefile_ai.application.contradiction_contracts import (
    ContradictionRepository,
    ContradictionResult,
)
from casefile_ai.application.services.claim_coverage import DefaultClaimCoverageService
from casefile_ai.application.services.claims import (
    EXTRACTION_VERSION,
    DefaultClaimExtractionService,
)
from casefile_ai.application.services.conflict_candidates import DefaultConflictCandidateService
from casefile_ai.application.services.contradictions import (
    DETECTOR_VERSION,
    DefaultContradictionService,
)
from casefile_ai.application.services.timeline import DefaultTimelineService, TimelineEvent
from casefile_ai.domain.exceptions import CaseNotFoundError


class AnswerCatalog(Protocol):
    def list_case_answers(self, case_id: str) -> tuple[GroundedAnswer, ...]: ...

    def case_id_for_answer(self, answer_id: str) -> str | None: ...


class CaseWorkspaceService:
    """Coordinates existing validated services; adds no factual decision rules."""

    def __init__(
        self,
        cases: CaseService,
        answers: AnswerCatalog,
        claims: ClaimRepository,
        contradictions: ContradictionRepository,
        extraction: DefaultClaimExtractionService,
        coverage: DefaultClaimCoverageService,
        candidates: DefaultConflictCandidateService,
        detector: DefaultContradictionService,
        timeline: DefaultTimelineService,
        model_version: str,
    ) -> None:
        self._cases = cases
        self._answers = answers
        self._claims = claims
        self._contradictions = contradictions
        self._extraction = extraction
        self._coverage = coverage
        self._candidates = candidates
        self._detector = detector
        self._timeline = timeline
        self.model_version = model_version

    def answers(self, case_id: str) -> tuple[GroundedAnswer, ...]:
        self._cases.get_case(case_id)
        return self._answers.list_case_answers(case_id)

    def answer(self, case_id: str, answer_id: str) -> GroundedAnswer:
        for answer in self.answers(case_id):
            if answer.answer_id == answer_id:
                return answer
        raise CaseNotFoundError("answer does not belong to case")

    def extract(self, case_id: str, answer_id: str) -> tuple[ExtractedClaim, ...]:
        self.answer(case_id, answer_id)
        return self._extraction.extract_answer_claims(answer_id)

    def extract_by_answer(self, answer_id: str) -> tuple[ExtractedClaim, ...]:
        case_id = self._answers.case_id_for_answer(answer_id)
        if case_id is None:
            raise CaseNotFoundError("answer not found")
        return self.extract(case_id, answer_id)

    def coverage(self, case_id: str, answer_id: str) -> ClaimCoverageResult:
        self.answer(case_id, answer_id)
        return self._coverage.evaluate(answer_id, EXTRACTION_VERSION, self.model_version)

    def claims(self, case_id: str) -> tuple[ExtractedClaim, ...]:
        self._cases.get_case(case_id)
        return tuple(
            item.claim for item in self._claims.list_case_claims(
                case_id, EXTRACTION_VERSION, self.model_version
            )
        )

    def candidates(self, case_id: str) -> tuple[ConflictCandidate, ...]:
        self._cases.get_case(case_id)
        return self._candidates.generate(case_id, EXTRACTION_VERSION, self.model_version)

    def detect(self, case_id: str) -> tuple[ContradictionResult, ...]:
        self._cases.get_case(case_id)
        return self._detector.detect_case(case_id, EXTRACTION_VERSION, self.model_version)

    def results(self, case_id: str) -> tuple[ContradictionResult, ...]:
        self._cases.get_case(case_id)
        return tuple(
            item for item in self._contradictions.list_case_results(case_id)
            if item.extraction_version == EXTRACTION_VERSION
            and item.claim_model_version == self.model_version
            and item.detector_version == DETECTOR_VERSION
        )

    def timeline(self, case_id: str) -> tuple[TimelineEvent, ...]:
        self._cases.get_case(case_id)
        return self._timeline.build(case_id, EXTRACTION_VERSION, self.model_version)
