"""Persist atomic claims only through citations of their validated source answer."""

from __future__ import annotations

import json
from typing import cast

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from casefile_ai.application.claim_contracts import (
    CaseClaim,
    ClaimExtractionError,
    ClaimKind,
    ExtractedClaim,
)
from casefile_ai.domain.ids import derived_id
from casefile_ai.infrastructure.db.models import (
    AnswerCitationModel,
    AnswerModel,
    ClaimEvidenceModel,
    ClaimModel,
    QuestionModel,
)
from casefile_ai.infrastructure.db.session import session_scope


class SqlAlchemyClaimRepository:
    def __init__(self, factory: sessionmaker[Session]) -> None:
        self._factory = factory

    def get_claims(
        self, answer_id: str, extraction_version: str, model_version: str
    ) -> tuple[ExtractedClaim, ...]:
        with session_scope(self._factory) as session:
            return self._get_claims(session, answer_id, extraction_version, model_version)

    def list_case_claims(
        self, case_id: str, extraction_version: str, model_version: str
    ) -> tuple[CaseClaim, ...]:
        with session_scope(self._factory) as session:
            answer_ids = session.scalars(
                select(AnswerModel.answer_id)
                .join(QuestionModel, AnswerModel.question_id == QuestionModel.question_id)
                .join(ClaimModel, ClaimModel.answer_id == AnswerModel.answer_id)
                .where(
                    QuestionModel.case_id == case_id,
                    AnswerModel.status == "answered",
                    AnswerModel.validation_status == "validated",
                    ClaimModel.extraction_version == extraction_version,
                    ClaimModel.model_version == model_version,
                )
                .distinct()
                .order_by(AnswerModel.answer_id)
            ).all()
            return tuple(
                CaseClaim(case_id, claim)
                for answer_id in answer_ids
                for claim in self._get_claims(session, answer_id, extraction_version, model_version)
            )

    @staticmethod
    def _get_claims(
        session: Session, answer_id: str, extraction_version: str, model_version: str
    ) -> tuple[ExtractedClaim, ...]:
        rows = session.scalars(
            select(ClaimModel)
            .where(
                ClaimModel.answer_id == answer_id,
                ClaimModel.extraction_version == extraction_version,
                ClaimModel.model_version == model_version,
            )
            .order_by(ClaimModel.claim_id)
        ).all()
        results: list[ExtractedClaim] = []
        for row in rows:
            links = session.scalars(
                select(AnswerCitationModel)
                .join(
                    ClaimEvidenceModel,
                    ClaimEvidenceModel.answer_citation_id == AnswerCitationModel.answer_citation_id,
                )
                .where(ClaimEvidenceModel.claim_id == row.claim_id)
                .order_by(AnswerCitationModel.citation_order)
            ).all()
            if not links or any(
                link.answer_id != row.answer_id or link.claim_id != row.source_answer_claim_id
                for link in links
            ):
                raise ClaimExtractionError("persisted claim citation lineage is invalid")
            evidence_ids = tuple(link.evidence_id for link in links)
            results.append(
                ExtractedClaim(
                    claim_id=row.claim_id,
                    answer_id=row.answer_id,
                    source_answer_claim_id=row.source_answer_claim_id,
                    text=row.text,
                    kind=cast(ClaimKind, row.kind),
                    evidence_ids=evidence_ids,
                    extraction_version=row.extraction_version,
                    model_version=row.model_version,
                )
            )
        return tuple(results)

    def save_claims(
        self, answer_id: str, claims: tuple[ExtractedClaim, ...]
    ) -> tuple[ExtractedClaim, ...]:
        if not claims:
            return ()
        with session_scope(self._factory) as session:
            answer = session.get(AnswerModel, answer_id)
            if (
                answer is None
                or answer.status != "answered"
                or answer.validation_status != "validated"
            ):
                raise ClaimExtractionError("source answer is not validated")
            source_ids = {str(item["claim_id"]) for item in json.loads(answer.claims_json)}
            links = session.scalars(
                select(AnswerCitationModel).where(AnswerCitationModel.answer_id == answer_id)
            ).all()
            allowed = {(link.claim_id, link.evidence_id): link for link in links}
            for claim in claims:
                if (
                    claim.answer_id != answer_id
                    or claim.source_answer_claim_id not in source_ids
                    or not claim.evidence_ids
                    or len(claim.evidence_ids) != len(set(claim.evidence_ids))
                    or claim.extraction_version != claims[0].extraction_version
                    or claim.model_version != claims[0].model_version
                ):
                    raise ClaimExtractionError("claim source or citations are invalid")
                selected = [
                    allowed.get((claim.source_answer_claim_id, evidence_id))
                    for evidence_id in claim.evidence_ids
                ]
                if any(link is None for link in selected):
                    raise ClaimExtractionError("claim citation is not in its source answer")
                expected_id = derived_id(
                    "atomic-claim",
                    answer_id,
                    claim.source_answer_claim_id,
                    claim.extraction_version,
                    claim.model_version,
                    claim.kind,
                    claim.text,
                    *claim.evidence_ids,
                )
                if claim.claim_id != expected_id:
                    raise ClaimExtractionError("claim stable ID is invalid")
            existing = self._get_claims(
                session, answer_id, claims[0].extraction_version, claims[0].model_version
            )
            if existing:
                return existing
            for claim in claims:
                session.add(
                    ClaimModel(
                        claim_id=claim.claim_id,
                        answer_id=claim.answer_id,
                        source_answer_claim_id=claim.source_answer_claim_id,
                        text=claim.text,
                        kind=claim.kind,
                        extraction_version=claim.extraction_version,
                        model_version=claim.model_version,
                    )
                )
            session.flush()
            for claim in claims:
                for evidence_id in claim.evidence_ids:
                    link = allowed[(claim.source_answer_claim_id, evidence_id)]
                    session.add(
                        ClaimEvidenceModel(
                            claim_evidence_id=derived_id(
                                "claim-evidence", claim.claim_id, link.answer_citation_id
                            ),
                            claim_id=claim.claim_id,
                            answer_citation_id=link.answer_citation_id,
                        )
                    )
            session.flush()
            return self._get_claims(
                session, answer_id, claims[0].extraction_version, claims[0].model_version
            )
