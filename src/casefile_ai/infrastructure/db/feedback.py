"""SQLite persistence for local answer feedback."""

from __future__ import annotations

import builtins
import json
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from casefile_ai.application.feedback_contracts import FeedbackError
from casefile_ai.infrastructure.db.models import (
    AnswerCitationModel,
    AnswerModel,
    CaseModel,
    ChunkModel,
    DocumentVersionModel,
    EvidenceModel,
    FeedbackModel,
    PageModel,
    QuestionModel,
)
from casefile_ai.infrastructure.db.session import session_scope


class SqlAlchemyFeedbackRepository:
    def __init__(self, factory: sessionmaker[Session]) -> None:
        self._factory = factory

    @staticmethod
    def _record(model: FeedbackModel) -> dict[str, Any]:
        return {
            "feedback_id": model.feedback_id,
            "case_id": model.case_id,
            "answer_id": model.answer_id,
            "rating": model.rating,
            "category": model.category,
            "note": model.note,
            "review_status": model.review_status,
            "reviewed_at": model.reviewed_at.isoformat() if model.reviewed_at else None,
            "expected_status": model.expected_status,
            "expected_answer": model.expected_answer,
            "expected_evidence": json.loads(model.expected_evidence_json)
            if model.expected_evidence_json
            else None,
            "created_at": model.created_at.isoformat(),
        }

    @staticmethod
    def _case(session: Session, case_id: str | None) -> None:
        if case_id is not None and session.get(CaseModel, case_id) is None:
            raise FeedbackError("case_not_found")

    def add(
        self, case_id: str, answer_id: str, rating: str, category: str, note: str
    ) -> dict[str, Any]:
        with session_scope(self._factory) as session:
            self._case(session, case_id)
            binding = session.execute(
                select(QuestionModel.case_id)
                .join(AnswerModel)
                .where(AnswerModel.answer_id == answer_id)
            ).scalar_one_or_none()
            if binding is None:
                raise FeedbackError("answer_not_found")
            if binding != case_id:
                raise FeedbackError("cross_case_answer")
            model = FeedbackModel(
                feedback_id=str(uuid4()),
                case_id=case_id,
                answer_id=answer_id,
                rating=rating,
                category=category,
                note=note,
                review_status="pending",
            )
            session.add(model)
            session.flush()
            return self._record(model)

    def list(self, case_id: str | None = None) -> builtins.list[dict[str, Any]]:
        with session_scope(self._factory) as session:
            self._case(session, case_id)
            query = select(FeedbackModel).order_by(
                FeedbackModel.created_at, FeedbackModel.feedback_id
            )
            if case_id is not None:
                query = query.where(FeedbackModel.case_id == case_id)
            return [self._record(model) for model in session.scalars(query)]

    def review(
        self,
        feedback_id: str,
        status: str,
        expected_status: str | None,
        expected_answer: str | None,
        expected_evidence: builtins.list[dict[str, Any]] | None,
    ) -> dict[str, Any]:
        with session_scope(self._factory) as session:
            model = session.get(FeedbackModel, feedback_id)
            if model is None:
                raise FeedbackError("feedback_not_found")
            if model.review_status != "pending":
                raise FeedbackError("already_reviewed")
            model.review_status = status
            model.reviewed_at = datetime.now(UTC)
            model.expected_status = expected_status
            model.expected_answer = expected_answer
            model.expected_evidence_json = (
                json.dumps(expected_evidence, ensure_ascii=False)
                if expected_evidence is not None
                else None
            )
            session.flush()
            return self._record(model)

    def review_rows(self, case_id: str | None = None) -> builtins.list[dict[str, Any]]:
        with session_scope(self._factory) as session:
            self._case(session, case_id)
            query = (
                select(FeedbackModel, QuestionModel.question, AnswerModel.answer)
                .join(AnswerModel, FeedbackModel.answer_id == AnswerModel.answer_id)
                .join(QuestionModel, AnswerModel.question_id == QuestionModel.question_id)
                .order_by(FeedbackModel.created_at, FeedbackModel.feedback_id)
            )
            if case_id is not None:
                query = query.where(FeedbackModel.case_id == case_id)
            rows: builtins.list[dict[str, Any]] = []
            for model, question, answer in session.execute(query):
                row = self._record(model)
                row.update(question=question, answer=answer)
                citations = session.execute(
                    select(AnswerCitationModel, EvidenceModel, PageModel, DocumentVersionModel)
                    .join(
                        EvidenceModel, AnswerCitationModel.evidence_id == EvidenceModel.evidence_id
                    )
                    .join(ChunkModel, EvidenceModel.chunk_id == ChunkModel.chunk_id)
                    .join(PageModel, ChunkModel.page_id == PageModel.page_id)
                    .join(
                        DocumentVersionModel,
                        PageModel.document_version_id == DocumentVersionModel.document_version_id,
                    )
                    .where(AnswerCitationModel.answer_id == model.answer_id)
                    .order_by(AnswerCitationModel.citation_order)
                ).all()
                row["citations"] = [
                    {
                        "evidence_id": evidence.evidence_id,
                        "document_version_id": version.document_version_id,
                        "filename": version.filename,
                        "page": page.page_number,
                        "snippet": evidence.quote,
                    }
                    for _, evidence, page, version in citations
                ]
                rows.append(row)
            return rows
