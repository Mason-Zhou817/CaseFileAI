from __future__ import annotations

import json
from pathlib import Path

import numpy as np
from alembic import command
from alembic.config import Config

from casefile_ai.application.bootstrap import build_case_application
from casefile_ai.application.qa_contracts import PersistAnswerDraft
from casefile_ai.application.services.documents import DefaultIngestionService
from casefile_ai.application.services.retrieval import DefaultRetrievalService
from casefile_ai.domain.ids import derived_id, new_id
from casefile_ai.infrastructure.db.documents import SqlAlchemyDocumentRepository
from casefile_ai.infrastructure.db.qa import SqlAlchemyQARepository
from casefile_ai.infrastructure.db.session import build_session_factory
from casefile_ai.infrastructure.retrieval.repository import SqliteChunkRepository

ROOT = Path(__file__).resolve().parents[2]
TEXT_PDF = ROOT / "tests/fixtures/synthetic_case/01_询问笔录_王海.pdf"


class QAEmbedding:
    model_version = "qa-embedding-v1"

    @staticmethod
    def _vector(text: str) -> list[float]:
        vector = np.asarray(
            [float("陈军" in text), float("2021年3月5日" in text), 0.01],
            dtype=np.float32,
        )
        vector /= np.linalg.norm(vector)
        return vector.tolist()

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [self._vector(text) for text in texts]

    def embed_query(self, text: str) -> list[float]:
        return self._vector(text)


def _migrate(url: str) -> None:
    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(ROOT / "migrations"))
    config.set_main_option("sqlalchemy.url", url)
    command.upgrade(config, "head")


def test_evidence_lineage_answer_cache_and_restart(tmp_path: Path) -> None:
    url = f"sqlite:///{(tmp_path / 'qa.db').as_posix()}"
    _migrate(url)
    application = build_case_application(url)
    factory = build_session_factory(application.engine)
    ingestion = DefaultIngestionService(SqlAlchemyDocumentRepository(factory), tmp_path)
    retrieval = DefaultRetrievalService(SqliteChunkRepository(factory), QAEmbedding())
    repository = SqlAlchemyQARepository(factory)
    case = application.service.create_case("问答持久化案件")
    other_case = application.service.create_case("其他案件")
    ingestion.import_document(case.case_id, TEXT_PDF)
    retrieval.index_case(case.case_id)

    state = repository.index_state(
        case.case_id,
        "page-char-v1-target500-overlap80-boundary100",
        QAEmbedding.model_version,
    )
    assert state.complete is True
    hits = retrieval.search_case_evidence(case.case_id, "陈军", 2)
    evidence = repository.materialize_evidence(case.case_id, hits)
    assert evidence
    assert evidence[0].quote == hits[0].quote
    assert evidence[0].document_version_id == hits[0].document_version_id
    assert repository.resolve_evidence(case.case_id, (evidence[0].evidence_id,))
    assert repository.resolve_evidence(other_case.case_id, (evidence[0].evidence_id,)) == {}

    claim_id = derived_id("answer-claim", "answer-1", 0)
    claims = [
        {
            "claim_id": claim_id,
            "text": "陈军是相关人物。",
            "citation_ids": [evidence[0].evidence_id],
            "confidence": 0.93,
            "support_status": "supported",
        }
    ]
    question_id = new_id()
    draft = PersistAnswerDraft(
        answer_id="answer-1",
        question_id=question_id,
        case_id=case.case_id,
        question="谁是相关人物？",
        normalized_question="谁是相关人物？",
        retrieval_limit=2,
        status="answered",
        answer="陈军是相关人物。",
        claims_json=json.dumps(claims, ensure_ascii=False),
        evidence_ids_json=json.dumps([item.evidence_id for item in evidence]),
        citations=((claim_id, evidence[0].evidence_id),),
        model_version="fake-qa-v1",
        prompt_version="grounded-qa-v1",
        retrieval_version="hybrid-rrf-v1",
        embedding_version=QAEmbedding.model_version,
        chunking_version="page-char-v1-target500-overlap80-boundary100",
        source_fingerprint=state.source_fingerprint,
        index_fingerprint=state.index_fingerprint,
        cache_key="a" * 64,
        validation_status="validated",
        refusal_reason=None,
        retrieval_ms=1.0,
        generation_ms=2.0,
        validation_ms=3.0,
        total_ms=6.0,
    )
    saved = repository.save_answer(draft)
    assert saved.answer == "陈军是相关人物。"
    assert saved.citations[0].filename == TEXT_PDF.name
    assert saved.citations[0].page_number >= 1
    assert saved.citations[0].document_version_id == evidence[0].document_version_id
    application.engine.dispose()

    restarted = build_case_application(url)
    restarted_repository = SqlAlchemyQARepository(build_session_factory(restarted.engine))
    historical = restarted_repository.get_answer("answer-1")
    assert historical is not None
    assert historical.citations == saved.citations
    cached = restarted_repository.get_cached_answer("a" * 64)
    assert cached is not None and cached.metadata is not None
    assert cached.metadata.cache_hit is True
    restarted.engine.dispose()
