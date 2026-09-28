"""Build a case timeline from validated claims and their original citations."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from casefile_ai.application.contracts import AnswerCitation
from casefile_ai.application.contradiction_contracts import ContradictionRepository
from casefile_ai.application.services.claim_coverage import (
    AnswerLookup,
    DefaultClaimCoverageService,
)
from casefile_ai.application.services.conflict_candidates import CaseClaimLookup
from casefile_ai.application.services.contradictions import DETECTOR_VERSION, _date_value
from casefile_ai.domain.ids import derived_id

TIMELINE_VERSION = "claim-timeline-v1"
DateStatus = Literal["exact", "partial", "unknown"]


@dataclass(frozen=True, slots=True)
class TimelineEvent:
    event_id: str
    case_id: str
    date_status: DateStatus
    date_value: str | None
    fact: str
    claim_id: str
    evidence: tuple[AnswerCitation, ...]
    contradiction_result_ids: tuple[str, ...]
    extraction_version: str
    claim_model_version: str
    timeline_version: str


class DefaultTimelineService:
    def __init__(
        self, answers: AnswerLookup, claims: CaseClaimLookup,
        contradictions: ContradictionRepository,
    ) -> None:
        self._claims = claims
        self._coverage = DefaultClaimCoverageService(answers, claims)
        self._contradictions = contradictions

    def build(
        self, case_id: str, extraction_version: str, model_version: str
    ) -> tuple[TimelineEvent, ...]:
        linked: dict[str, set[str]] = {}
        for result in self._contradictions.list_case_results(case_id):
            if (result.case_id != case_id or result.extraction_version != extraction_version
                or result.claim_model_version != model_version
                or result.detector_version != DETECTOR_VERSION):
                continue
            for claim_id in (result.left_claim_id, result.right_claim_id):
                linked.setdefault(claim_id, set()).add(result.result_id)
        checked: dict[str, dict[str, tuple[AnswerCitation, ...]]] = {}
        events: dict[str, TimelineEvent] = {}
        for item in self._claims.list_case_claims(case_id, extraction_version, model_version):
            claim = item.claim
            if (item.case_id != case_id or claim.extraction_version != extraction_version
                or claim.model_version != model_version):
                continue
            if claim.answer_id not in checked:
                coverage = self._coverage.evaluate(
                    claim.answer_id, extraction_version, model_version
                )
                checked[claim.answer_id] = {
                    entry.claim_id: entry.evidence for entry in coverage.claims
                    if entry.issue is None and entry.evidence
                }
            evidence = checked[claim.answer_id].get(claim.claim_id)
            if evidence is None:
                continue
            parsed = _date_value(claim.text)
            if parsed is None:
                status: DateStatus = "unknown"
                value = None
            else:
                components = parsed[0]
                status = "exact" if len(components) >= 3 else "partial"
                value = str(components[0])
                if len(components) >= 2:
                    value += f"-{components[1]:02d}"
                if len(components) >= 3:
                    value += f"-{components[2]:02d}"
                if len(components) >= 5:
                    value += f"T{components[3]:02d}:{components[4]:02d}"
            event = TimelineEvent(
                event_id=derived_id("timeline-event", TIMELINE_VERSION, case_id,
                                    claim.claim_id),
                case_id=case_id, date_status=status, date_value=value,
                fact=claim.text, claim_id=claim.claim_id, evidence=evidence,
                contradiction_result_ids=tuple(sorted(linked.get(claim.claim_id, set()))),
                extraction_version=extraction_version, claim_model_version=model_version,
                timeline_version=TIMELINE_VERSION,
            )
            events[event.event_id] = event
        return tuple(sorted(
            events.values(),
            key=lambda event: (event.date_value is None, event.date_value or "",
                               event.date_status != "partial", event.event_id),
        ))
