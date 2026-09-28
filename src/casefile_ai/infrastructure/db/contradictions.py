"""Persist contradiction decisions only when both claim citation chains are valid."""

from __future__ import annotations

import json
from dataclasses import asdict
from typing import cast

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from casefile_ai.application.claim_contracts import ClaimKind
from casefile_ai.application.contracts import AnswerCitation
from casefile_ai.application.contradiction_contracts import (
    ContradictionResult,
    ContradictionValidationError,
    Verdict,
)
from casefile_ai.application.services.contradictions import DETECTOR_VERSION, PROMPT_VERSION
from casefile_ai.domain.ids import derived_id
from casefile_ai.infrastructure.db.models import (
    AnswerCitationModel,
    AnswerModel,
    ChunkModel,
    ClaimEvidenceModel,
    ClaimModel,
    ContradictionResultModel,
    DocumentModel,
    DocumentVersionModel,
    EvidenceModel,
    PageModel,
    QuestionModel,
)
from casefile_ai.infrastructure.db.session import session_scope


class SqlAlchemyContradictionRepository:
    def __init__(self, factory: sessionmaker[Session]) -> None:
        self._factory = factory

    def get_result(self, result_id: str) -> ContradictionResult | None:
        with session_scope(self._factory) as session:
            row = session.get(ContradictionResultModel, result_id)
            return self._to_result(row) if row is not None else None

    def list_case_results(self, case_id: str) -> tuple[ContradictionResult, ...]:
        with session_scope(self._factory) as session:
            rows = session.scalars(
                select(ContradictionResultModel)
                .where(ContradictionResultModel.case_id == case_id)
                .order_by(ContradictionResultModel.result_id)
            ).all()
            return tuple(self._to_result(row) for row in rows)

    def save_result(self, result: ContradictionResult) -> ContradictionResult:
        with session_scope(self._factory) as session:
            existing = session.get(ContradictionResultModel, result.result_id)
            if existing is not None:
                loaded = self._to_result(existing)
                if loaded != result:
                    raise ContradictionValidationError(
                        "result ID already holds a different decision"
                    )
                return loaded
            self._validate(session, result)
            session.add(ContradictionResultModel(
                result_id=result.result_id, case_id=result.case_id,
                candidate_id=result.candidate_id, left_claim_id=result.left_claim_id,
                right_claim_id=result.right_claim_id,
                contradiction_type=result.contradiction_type, verdict=result.verdict,
                reason=result.reason, basis=result.basis,
                left_evidence_json=json.dumps([asdict(e) for e in result.left_evidence],
                                              ensure_ascii=False),
                right_evidence_json=json.dumps([asdict(e) for e in result.right_evidence],
                                               ensure_ascii=False),
                detector_version=result.detector_version, prompt_version=result.prompt_version,
                model_version=result.model_version,
                extraction_version=result.extraction_version,
                claim_model_version=result.claim_model_version,
            ))
            session.flush()
            return result

    @staticmethod
    def _validate(session: Session, result: ContradictionResult) -> None:
        allowed_basis = {
            "date": {
                "contradiction": {"normalized_date_mismatch", "normalized_time_mismatch"},
                "non_contradiction": {"normalized_date_compatible", "normalized_time_equal"},
                "uncertain": {"date_parse_incomplete", "date_scope_unaligned"},
            },
            "amount": {
                "contradiction": {"normalized_amount_mismatch"},
                "non_contradiction": {"normalized_amount_equal"},
                "uncertain": {"amount_parse_incomplete", "amount_scope_unaligned",
                              "currency_unaligned"},
            },
            "action": {
                "contradiction": {"model_incompatible_same_slot"},
                "non_contradiction": {"different_event_or_slot", "model_compatible"},
                "uncertain": {"model_unavailable", "model_output_invalid", "model_uncertain",
                              "action_polarity_unverified"},
            },
            "person": {
                "contradiction": {"model_incompatible_same_slot"},
                "non_contradiction": {"different_event_or_slot", "model_compatible"},
                "uncertain": {"model_unavailable", "model_output_invalid", "model_uncertain",
                              "identity_unverified", "identity_not_extractive",
                              "identity_not_exclusive", "identity_alias_possible"},
            },
        }
        if (not result.reason.strip() or result.detector_version != DETECTOR_VERSION
            or result.basis not in allowed_basis.get(result.contradiction_type, {}).get(
                result.verdict, set()
            ) or (result.contradiction_type in {"date", "amount"} and
                  (result.prompt_version != "none" or result.model_version != "none"))
            or (result.contradiction_type in {"action", "person"} and
                result.prompt_version != PROMPT_VERSION)):
            raise ContradictionValidationError("result verdict, basis, or version invalid")
        left = session.get(ClaimModel, result.left_claim_id)
        right = session.get(ClaimModel, result.right_claim_id)
        if (left is None or right is None or left.claim_id == right.claim_id
            or left.kind != right.kind or left.kind != result.contradiction_type
            or left.kind not in {"date", "amount", "action", "person"}
            or result.verdict not in {"contradiction", "non_contradiction", "uncertain"}
            or any(claim.extraction_version != result.extraction_version or
                   claim.model_version != result.claim_model_version
                   for claim in (left, right))):
            raise ContradictionValidationError("result factual type or claims invalid")
        expected_candidate = derived_id(
            "conflict-candidate-v1", result.case_id, left.claim_id, right.claim_id,
            result.extraction_version, result.claim_model_version,
        )
        expected_result = derived_id(
            "contradiction-result", expected_candidate, result.detector_version,
            result.prompt_version if left.kind in {"action", "person"} else "deterministic",
            result.model_version if left.kind in {"action", "person"}
            and result.model_version != "unavailable" else "none",
        )
        if result.candidate_id != expected_candidate or result.result_id != expected_result:
            raise ContradictionValidationError("candidate or result stable ID invalid")
        for claim, supplied in ((left, result.left_evidence), (right, result.right_evidence)):
            answer = session.get(AnswerModel, claim.answer_id)
            question = session.get(QuestionModel, answer.question_id) if answer else None
            if (answer is None or question is None or question.case_id != result.case_id
                or answer.status != "answered" or answer.validation_status != "validated"
                or not supplied):
                raise ContradictionValidationError("cross-case or unvalidated claim")
            rows = session.execute(
                select(AnswerCitationModel, EvidenceModel, ChunkModel, PageModel,
                       DocumentVersionModel, DocumentModel)
                .join(ClaimEvidenceModel,
                      ClaimEvidenceModel.answer_citation_id ==
                      AnswerCitationModel.answer_citation_id)
                .join(EvidenceModel, EvidenceModel.evidence_id == AnswerCitationModel.evidence_id)
                .join(ChunkModel, ChunkModel.chunk_id == EvidenceModel.chunk_id)
                .join(PageModel, PageModel.page_id == ChunkModel.page_id)
                .join(DocumentVersionModel,
                      DocumentVersionModel.document_version_id == PageModel.document_version_id)
                .join(DocumentModel, DocumentModel.document_id == DocumentVersionModel.document_id)
                .where(ClaimEvidenceModel.claim_id == claim.claim_id)
                .order_by(AnswerCitationModel.citation_order)
            ).all()
            expected: list[AnswerCitation] = []
            for link, evidence, chunk, page, version, document in rows:
                if (link.answer_id != claim.answer_id
                    or link.claim_id != claim.source_answer_claim_id
                    or any(case != result.case_id for case in
                           (evidence.case_id, chunk.case_id, document.case_id))
                    or evidence.quote != chunk.text
                    or page.text[evidence.start_offset:evidence.end_offset] != evidence.quote):
                    raise ContradictionValidationError("claim evidence lineage invalid")
                expected.append(AnswerCitation(
                    evidence.evidence_id, link.claim_id, document.document_id,
                    page.page_id, version.filename, page.page_number, evidence.quote,
                    version.document_version_id,
                ))
            if len(supplied) != len(expected) or set(supplied) != set(expected):
                raise ContradictionValidationError("result evidence is not claim evidence")

    @staticmethod
    def _to_result(row: ContradictionResultModel) -> ContradictionResult:
        try:
            left = tuple(AnswerCitation(**item) for item in json.loads(row.left_evidence_json))
            right = tuple(AnswerCitation(**item) for item in json.loads(row.right_evidence_json))
        except (TypeError, ValueError, KeyError) as exc:
            raise ContradictionValidationError("stored result citations invalid") from exc
        return ContradictionResult(
            row.result_id, row.case_id, row.candidate_id, row.left_claim_id,
            row.right_claim_id, cast(ClaimKind, row.contradiction_type),
            cast(Verdict, row.verdict), row.reason, row.basis, left, right,
            row.detector_version, row.prompt_version, row.model_version,
            row.extraction_version, row.claim_model_version,
        )
