from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest

from casefile_ai.application.claim_contracts import ConflictCandidate, ExtractedClaim
from casefile_ai.application.contracts import AnswerCitation
from casefile_ai.application.contradiction_contracts import ContradictionValidationError
from casefile_ai.application.services.contradictions import DefaultContradictionService
from casefile_ai.infrastructure.db.contradictions import SqlAlchemyContradictionRepository
from casefile_ai.infrastructure.db.models import (
    AnswerCitationModel,
    AnswerModel,
    CaseModel,
    ChunkModel,
    ClaimEvidenceModel,
    ClaimModel,
    DocumentModel,
    DocumentVersionModel,
    EvidenceModel,
    PageModel,
    QuestionModel,
)
from casefile_ai.infrastructure.db.session import (
    build_engine,
    build_session_factory,
    initialize_schema,
    session_scope,
)


class Source:
    def __init__(self, item: ConflictCandidate) -> None:
        self.item = item

    def generate(self, case_id: str, extraction_version: str,
                 model_version: str) -> tuple[ConflictCandidate, ...]:
        return (self.item,)


def _seed_case(factory: object) -> tuple[ConflictCandidate, AnswerCitation]:
    # A small synthetic citation chain with a second case for cross-case rejection.
    with session_scope(factory) as session:  # type: ignore[arg-type]
        session.add_all([CaseModel(case_id="case", name="合成案件"),
                         CaseModel(case_id="foreign", name="另案")])
        session.flush()
        def add(row: object) -> None:
            session.add(row)
            session.flush()

        claims: list[ExtractedClaim] = []
        citations: list[AnswerCitation] = []
        for index, (case_id, text) in enumerate((
            ("case", "交易金额为450000元。"),
            ("case", "交易金额为500000元。"),
            ("foreign", "交易金额为700000元。"),
        ), start=1):
            i = str(index)
            add(DocumentModel(document_id="d" + i, case_id=case_id))
            add(DocumentVersionModel(
                document_version_id="v" + i, document_id="d" + i,
                version_number=1, filename="合成.pdf", content_sha256=i * 64,
                storage_key="storage" + i, page_count=1, parser_version="test",
            ))
            add(PageModel(
                page_id="p" + i, document_version_id="v" + i, page_number=1,
                text=text, text_hash=i * 64, ocr_used=False,
            ))
            add(ChunkModel(
                chunk_id="chunk" + i, case_id=case_id, page_id="p" + i,
                chunk_index=0, text=text, text_hash=i * 64,
                start_offset=0, end_offset=len(text), chunking_version="test",
            ))
            add(EvidenceModel(
                evidence_id="e" + i, case_id=case_id, chunk_id="chunk" + i,
                quote=text, start_offset=0, end_offset=len(text), evidence_type="qa",
            ))
            add(QuestionModel(
                question_id="q" + i, case_id=case_id, question="金额？",
                normalized_question="金额？" + i, retrieval_limit=1,
            ))
            add(AnswerModel(
                answer_id="a" + i, question_id="q" + i, status="answered",
                answer=text, claims_json=json.dumps([{"claim_id": "s" + i, "text": text}]),
                evidence_ids_json=json.dumps(["e" + i]), model_version="qa-fake",
                prompt_version="qa", retrieval_version="r", embedding_version="e",
                chunking_version="c", source_fingerprint=i * 64,
                index_fingerprint=i * 64, cache_key=("a" + i) * 32,
                validation_status="validated", refusal_reason=None,
                retrieval_ms=0, generation_ms=0, validation_ms=0, total_ms=0,
            ))
            add(AnswerCitationModel(
                answer_citation_id="ac" + i, answer_id="a" + i,
                evidence_id="e" + i, claim_id="s" + i, citation_order=0,
            ))
            add(ClaimModel(
                claim_id="c" + i, answer_id="a" + i,
                source_answer_claim_id="s" + i, text=text, kind="amount",
                extraction_version="v2", model_version="claim-fake",
            ))
            add(ClaimEvidenceModel(
                claim_evidence_id="ce" + i, claim_id="c" + i,
                answer_citation_id="ac" + i,
            ))
            claims.append(ExtractedClaim("c" + i, "a" + i, "s" + i, text,
                                         "amount", ("e" + i,), "v2", "claim-fake"))
            citations.append(AnswerCitation("e" + i, "s" + i, "d" + i, "p" + i,
                                            "合成.pdf", 1, text, "v" + i))
    item = ConflictCandidate("case", claims[0], claims[1],
                             (citations[0],), (citations[1],), "payment")
    return item, citations[2]


def test_persisted_result_is_idempotent_and_rejects_foreign_evidence(tmp_path: Path) -> None:
    engine = build_engine(f"sqlite:///{(tmp_path / 'contradictions.db').as_posix()}")
    initialize_schema(engine)
    factory = build_session_factory(engine)
    candidate, foreign = _seed_case(factory)
    repository = SqlAlchemyContradictionRepository(factory)
    service = DefaultContradictionService(Source(candidate), repository)
    first = service.detect_case("case", "v2", "claim-fake")[0]
    assert first.verdict == "contradiction"
    assert service.detect_case("case", "v2", "claim-fake") == (first,)
    assert repository.list_case_results("case") == (first,)
    with session_scope(factory) as session:
        with pytest.raises(ContradictionValidationError, match="evidence"):
            repository._validate(session, replace(first, right_evidence=(foreign,)))
        with pytest.raises(ContradictionValidationError, match="verdict"):
            repository._validate(session, replace(first, verdict="uncertain"))
    foreign_service = DefaultContradictionService(
        Source(replace(candidate, case_id="foreign")), repository
    )
    with pytest.raises(ContradictionValidationError, match="cross-case"):
        foreign_service.detect_case("foreign", "v2", "claim-fake")
    assert len(repository.list_case_results("case")) == 1
    engine.dispose()
