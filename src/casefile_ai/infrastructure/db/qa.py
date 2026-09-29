"""SQLAlchemy persistence and lineage resolution for grounded question answering."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Sequence
from typing import Any

from sqlalchemy import bindparam, func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from casefile_ai.application.contracts import (
    AnswerCitation,
    AnswerClaim,
    GroundedAnswer,
    QARunMetadata,
    SearchHit,
)
from casefile_ai.application.qa_contracts import (
    CaseIndexState,
    CitationValidationError,
    EvidenceRecord,
    PersistAnswerDraft,
)
from casefile_ai.application.retrieval_contracts import IndexablePage
from casefile_ai.application.services.chunking import chunk_page
from casefile_ai.domain.exceptions import CaseNotFoundError
from casefile_ai.domain.ids import derived_id
from casefile_ai.infrastructure.db.models import (
    AnswerCitationModel,
    AnswerModel,
    CaseModel,
    ChunkEmbeddingModel,
    ChunkModel,
    DocumentModel,
    DocumentVersionModel,
    EvidenceModel,
    PageModel,
    QuestionModel,
)
from casefile_ai.infrastructure.db.session import session_scope
from casefile_ai.infrastructure.retrieval.chinese_fts import TOKENIZATION_VERSION


def _fingerprint(values: Iterable[object]) -> str:
    payload = json.dumps(list(values), ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _record(
    evidence: EvidenceModel,
    chunk: ChunkModel,
    page: PageModel,
    version: DocumentVersionModel,
    document: DocumentModel,
) -> EvidenceRecord:
    return EvidenceRecord(
        evidence_id=evidence.evidence_id,
        case_id=evidence.case_id,
        chunk_id=chunk.chunk_id,
        document_id=document.document_id,
        document_version_id=version.document_version_id,
        page_id=page.page_id,
        filename=version.filename,
        page_number=page.page_number,
        quote=evidence.quote,
        start_offset=evidence.start_offset,
        end_offset=evidence.end_offset,
        evidence_type=evidence.evidence_type,
    )


class SqlAlchemyQARepository:
    def __init__(self, factory: sessionmaker[Session]) -> None:
        self._factory = factory

    @staticmethod
    def _require_case(session: Session, case_id: str) -> None:
        if session.get(CaseModel, case_id) is None:
            raise CaseNotFoundError(case_id)

    def index_state(
        self, case_id: str, chunking_version: str, embedding_version: str
    ) -> CaseIndexState:
        with session_scope(self._factory) as session:
            self._require_case(session, case_id)
            latest = (
                select(
                    DocumentVersionModel.document_id.label("document_id"),
                    func.max(DocumentVersionModel.version_number).label("version_number"),
                )
                .group_by(DocumentVersionModel.document_id)
                .subquery()
            )
            page_rows = session.execute(
                select(PageModel, DocumentVersionModel, DocumentModel)
                .join(
                    DocumentVersionModel,
                    DocumentVersionModel.document_version_id == PageModel.document_version_id,
                )
                .join(DocumentModel, DocumentModel.document_id == DocumentVersionModel.document_id)
                .join(
                    latest,
                    (latest.c.document_id == DocumentVersionModel.document_id)
                    & (latest.c.version_number == DocumentVersionModel.version_number),
                )
                .where(DocumentModel.case_id == case_id)
                .order_by(DocumentModel.document_id, PageModel.page_number)
            ).all()
            pages = [
                IndexablePage(
                    case_id=case_id,
                    document_id=document.document_id,
                    document_version_id=version.document_version_id,
                    page_id=page.page_id,
                    page_number=page.page_number,
                    text=page.text,
                )
                for page, version, document in page_rows
            ]
            expected_ids = [draft.chunk_id for page in pages for draft in chunk_page(page)]
            chunk_rows = (
                session.execute(
                    select(ChunkModel.chunk_id, ChunkEmbeddingModel.chunk_id)
                    .outerjoin(
                        ChunkEmbeddingModel,
                        (ChunkEmbeddingModel.chunk_id == ChunkModel.chunk_id)
                        & (ChunkEmbeddingModel.embedding_version == embedding_version),
                    )
                    .where(
                        ChunkModel.chunk_id.in_(expected_ids),
                        ChunkModel.chunking_version == chunking_version,
                    )
                ).all()
                if expected_ids
                else []
            )
            indexed_chunks = {str(row[0]) for row in chunk_rows}
            embedded_chunks = {str(row[1]) for row in chunk_rows if row[1] is not None}
            fts_rows: Sequence[Any] = ()
            if expected_ids:
                statement = text(
                    "SELECT chunk_id, tokenization_version FROM chunk_fts "
                    "WHERE chunk_id IN :chunk_ids"
                ).bindparams(bindparam("chunk_ids", expanding=True))
                fts_rows = session.execute(statement, {"chunk_ids": expected_ids}).all()
            fts_chunks = {
                str(row[0]) for row in fts_rows if str(row[1]) == TOKENIZATION_VERSION
            }
            expected = set(expected_ids)
            complete = expected.issubset(indexed_chunks & embedded_chunks & fts_chunks)
            source_values = [
                (
                    document.document_id,
                    version.document_version_id,
                    version.content_sha256,
                    version.parser_version,
                    page.page_id,
                    page.page_number,
                    page.text_hash,
                )
                for page, version, document in page_rows
            ]
            index_values = [
                (
                    chunk_id,
                    chunking_version,
                    embedding_version,
                    TOKENIZATION_VERSION,
                    chunk_id in indexed_chunks,
                    chunk_id in embedded_chunks,
                    chunk_id in fts_chunks,
                )
                for chunk_id in expected_ids
            ]
            return CaseIndexState(
                source_fingerprint=_fingerprint(source_values),
                index_fingerprint=_fingerprint(index_values),
                complete=complete,
            )

    def materialize_evidence(
        self, case_id: str, hits: list[SearchHit]
    ) -> list[EvidenceRecord]:
        if not hits:
            return []
        chunk_ids = [hit.chunk_id for hit in hits]
        with session_scope(self._factory) as session:
            self._require_case(session, case_id)
            rows = session.execute(
                select(ChunkModel, PageModel, DocumentVersionModel, DocumentModel)
                .join(PageModel, PageModel.page_id == ChunkModel.page_id)
                .join(
                    DocumentVersionModel,
                    DocumentVersionModel.document_version_id == PageModel.document_version_id,
                )
                .join(DocumentModel, DocumentModel.document_id == DocumentVersionModel.document_id)
                .where(ChunkModel.chunk_id.in_(chunk_ids), ChunkModel.case_id == case_id)
            ).all()
            by_chunk = {
                chunk.chunk_id: (chunk, page, version, document)
                for chunk, page, version, document in rows
            }
            records: list[EvidenceRecord] = []
            for hit in hits:
                source = by_chunk.get(hit.chunk_id)
                if source is None:
                    raise CitationValidationError("retrieved evidence source chain is incomplete")
                chunk, page, version, document = source
                expected_id = derived_id("evidence", chunk.chunk_id)
                if (
                    hit.evidence_id != expected_id
                    or hit.document_id != document.document_id
                    or hit.document_version_id != version.document_version_id
                    or hit.page_id != page.page_id
                    or hit.page_number != page.page_number
                    or hit.filename != version.filename
                    or hit.quote != chunk.text
                    or document.case_id != case_id
                    or page.text[chunk.start_offset : chunk.end_offset] != chunk.text
                ):
                    raise CitationValidationError(
                        "retrieved evidence metadata failed lineage validation"
                    )
                evidence = session.get(EvidenceModel, expected_id)
                if evidence is None:
                    evidence = EvidenceModel(
                        evidence_id=expected_id,
                        case_id=case_id,
                        chunk_id=chunk.chunk_id,
                        quote=chunk.text,
                        start_offset=chunk.start_offset,
                        end_offset=chunk.end_offset,
                        evidence_type="chunk",
                    )
                    session.add(evidence)
                    session.flush()
                elif (
                    evidence.case_id != case_id
                    or evidence.chunk_id != chunk.chunk_id
                    or evidence.quote != chunk.text
                    or evidence.start_offset != chunk.start_offset
                    or evidence.end_offset != chunk.end_offset
                    or evidence.evidence_type != "chunk"
                ):
                    raise CitationValidationError("persisted evidence identity is inconsistent")
                records.append(_record(evidence, chunk, page, version, document))
            return records

    def resolve_evidence(
        self, case_id: str, evidence_ids: tuple[str, ...]
    ) -> dict[str, EvidenceRecord]:
        if not evidence_ids:
            return {}
        with session_scope(self._factory) as session:
            self._require_case(session, case_id)
            rows = session.execute(
                select(
                    EvidenceModel,
                    ChunkModel,
                    PageModel,
                    DocumentVersionModel,
                    DocumentModel,
                )
                .join(ChunkModel, ChunkModel.chunk_id == EvidenceModel.chunk_id)
                .join(PageModel, PageModel.page_id == ChunkModel.page_id)
                .join(
                    DocumentVersionModel,
                    DocumentVersionModel.document_version_id == PageModel.document_version_id,
                )
                .join(DocumentModel, DocumentModel.document_id == DocumentVersionModel.document_id)
                .where(
                    EvidenceModel.evidence_id.in_(evidence_ids),
                    EvidenceModel.case_id == case_id,
                    ChunkModel.case_id == case_id,
                    DocumentModel.case_id == case_id,
                )
            ).all()
            resolved: dict[str, EvidenceRecord] = {}
            for evidence, chunk, page, version, document in rows:
                if (
                    evidence.evidence_id != derived_id("evidence", chunk.chunk_id)
                    or evidence.quote != chunk.text
                    or evidence.start_offset != chunk.start_offset
                    or evidence.end_offset != chunk.end_offset
                    or page.text[evidence.start_offset : evidence.end_offset] != evidence.quote
                    or not 1 <= page.page_number <= version.page_count
                    or not version.filename
                ):
                    continue
                resolved[evidence.evidence_id] = _record(
                    evidence, chunk, page, version, document
                )
            return resolved

    def get_cached_answer(self, cache_key: str) -> GroundedAnswer | None:
        with session_scope(self._factory) as session:
            answer = session.scalar(select(AnswerModel).where(AnswerModel.cache_key == cache_key))
            return self._hydrate_answer(session, answer, cache_hit=True) if answer else None

    def save_answer(self, draft: PersistAnswerDraft) -> GroundedAnswer:
        try:
            with session_scope(self._factory) as session:
                session.add(
                    QuestionModel(
                        question_id=draft.question_id,
                        case_id=draft.case_id,
                        question=draft.question,
                        normalized_question=draft.normalized_question,
                        retrieval_limit=draft.retrieval_limit,
                    )
                )
                session.flush()
                answer = AnswerModel(
                    answer_id=draft.answer_id,
                    question_id=draft.question_id,
                    status=draft.status,
                    answer=draft.answer,
                    claims_json=draft.claims_json,
                    evidence_ids_json=draft.evidence_ids_json,
                    model_version=draft.model_version,
                    prompt_version=draft.prompt_version,
                    retrieval_version=draft.retrieval_version,
                    embedding_version=draft.embedding_version,
                    chunking_version=draft.chunking_version,
                    source_fingerprint=draft.source_fingerprint,
                    index_fingerprint=draft.index_fingerprint,
                    cache_key=draft.cache_key,
                    validation_status=draft.validation_status,
                    refusal_reason=draft.refusal_reason,
                    retrieval_ms=draft.retrieval_ms,
                    generation_ms=draft.generation_ms,
                    validation_ms=draft.validation_ms,
                    total_ms=draft.total_ms,
                )
                session.add(answer)
                session.flush()
                for order, (claim_id, evidence_id) in enumerate(draft.citations):
                    session.add(
                        AnswerCitationModel(
                            answer_citation_id=derived_id(
                                "answer-citation", draft.answer_id, claim_id, evidence_id
                            ),
                            answer_id=draft.answer_id,
                            evidence_id=evidence_id,
                            claim_id=claim_id,
                            citation_order=order,
                        )
                    )
                session.flush()
                result = self._hydrate_answer(session, answer, cache_hit=False)
                assert result is not None
                return result
        except IntegrityError:
            cached = self.get_cached_answer(draft.cache_key)
            if cached is not None:
                return cached
            raise

    def get_answer(self, answer_id: str) -> GroundedAnswer | None:
        with session_scope(self._factory) as session:
            answer = session.get(AnswerModel, answer_id)
            return self._hydrate_answer(session, answer, cache_hit=False) if answer else None

    def case_id_for_answer(self, answer_id: str) -> str | None:
        with session_scope(self._factory) as session:
            return session.scalar(
                select(QuestionModel.case_id)
                .join(AnswerModel, AnswerModel.question_id == QuestionModel.question_id)
                .where(AnswerModel.answer_id == answer_id)
            )

    def list_case_answers(self, case_id: str) -> tuple[GroundedAnswer, ...]:
        with session_scope(self._factory) as session:
            self._require_case(session, case_id)
            rows = session.scalars(
                select(AnswerModel)
                .join(QuestionModel, AnswerModel.question_id == QuestionModel.question_id)
                .where(QuestionModel.case_id == case_id)
                .order_by(AnswerModel.created_at.desc(), AnswerModel.answer_id)
            ).all()
            return tuple(self._hydrate_answer(session, row, cache_hit=False) for row in rows)

    def _hydrate_answer(
        self, session: Session, answer: AnswerModel, *, cache_hit: bool
    ) -> GroundedAnswer:
        question = session.get(QuestionModel, answer.question_id)
        if question is None:
            raise CitationValidationError("persisted answer has no question")
        try:
            claims_data = json.loads(answer.claims_json)
            evidence_ids = tuple(str(item) for item in json.loads(answer.evidence_ids_json))
            claims = tuple(
                AnswerClaim(
                    claim_id=str(item["claim_id"]),
                    text=str(item["text"]),
                    citation_ids=tuple(str(value) for value in item["citation_ids"]),
                    confidence=float(item["confidence"]),
                    support_status=str(item["support_status"]),  # type: ignore[arg-type]
                )
                for item in claims_data
            )
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise CitationValidationError("persisted answer payload is invalid") from exc
        citation_rows = session.execute(
            select(
                AnswerCitationModel,
                EvidenceModel,
                ChunkModel,
                PageModel,
                DocumentVersionModel,
                DocumentModel,
            )
            .join(EvidenceModel, EvidenceModel.evidence_id == AnswerCitationModel.evidence_id)
            .join(ChunkModel, ChunkModel.chunk_id == EvidenceModel.chunk_id)
            .join(PageModel, PageModel.page_id == ChunkModel.page_id)
            .join(
                DocumentVersionModel,
                DocumentVersionModel.document_version_id == PageModel.document_version_id,
            )
            .join(DocumentModel, DocumentModel.document_id == DocumentVersionModel.document_id)
            .where(AnswerCitationModel.answer_id == answer.answer_id)
            .order_by(AnswerCitationModel.citation_order)
        ).all()
        citations: list[AnswerCitation] = []
        for link, evidence, chunk, page, version, document in citation_rows:
            if (
                document.case_id != question.case_id
                or evidence.case_id != question.case_id
                or chunk.case_id != question.case_id
                or evidence.quote != chunk.text
                or page.text[evidence.start_offset : evidence.end_offset] != evidence.quote
            ):
                raise CitationValidationError("persisted citation lineage is invalid")
            citations.append(
                AnswerCitation(
                    evidence_id=evidence.evidence_id,
                    claim_id=link.claim_id,
                    document_id=document.document_id,
                    page_id=page.page_id,
                    filename=version.filename,
                    page_number=page.page_number,
                    quote=evidence.quote,
                    document_version_id=version.document_version_id,
                )
            )
        cited_ids = {citation.evidence_id for citation in citations}
        required_ids = {evidence_id for claim in claims for evidence_id in claim.citation_ids}
        if cited_ids != required_ids or not required_ids.issubset(set(evidence_ids)):
            raise CitationValidationError("persisted citations do not match claims")
        metadata = QARunMetadata(
            retrieval_limit=question.retrieval_limit,
            retrieval_version=answer.retrieval_version,
            embedding_version=answer.embedding_version,
            chunking_version=answer.chunking_version,
            model_version=answer.model_version,
            prompt_version=answer.prompt_version,
            evidence_ids=evidence_ids,
            validation_status=answer.validation_status,
            refusal_reason=answer.refusal_reason,
            retrieval_ms=answer.retrieval_ms,
            generation_ms=answer.generation_ms,
            validation_ms=answer.validation_ms,
            total_ms=answer.total_ms,
            cache_hit=cache_hit,
        )
        return GroundedAnswer(
            answer_id=answer.answer_id,
            question=question.question,
            answer=answer.answer,
            citations=tuple(citations),
            abstained=answer.status == "insufficient_evidence",
            claims=claims,
            metadata=metadata,
        )
