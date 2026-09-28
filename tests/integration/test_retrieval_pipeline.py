from __future__ import annotations

from pathlib import Path

import numpy as np
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import func, select, text

from casefile_ai.application.bootstrap import build_case_application
from casefile_ai.application.services.documents import DefaultIngestionService
from casefile_ai.application.services.retrieval import DefaultRetrievalService
from casefile_ai.infrastructure.db.documents import SqlAlchemyDocumentRepository
from casefile_ai.infrastructure.db.models import ChunkEmbeddingModel, ChunkModel, PageModel
from casefile_ai.infrastructure.db.session import build_session_factory
from casefile_ai.infrastructure.retrieval.repository import SqliteChunkRepository
from casefile_ai.interfaces.api.main import create_app

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "tests/fixtures/synthetic_case"
TEXT_PDF = FIXTURES / "01_询问笔录_王海.pdf"
SCAN_PDF = FIXTURES / "02_银行凭证_扫描件.pdf"


class SemanticFakeEmbedding:
    model_version = "semantic-fake-v1"

    @staticmethod
    def _vector(value: str) -> list[float]:
        features = np.asarray(
            [
                float(any(term in value for term in ("失败", "未入账", "未能入账", "没有成功"))),
                float("陈军" in value),
                float(any(term in value for term in ("投资款", "转账", "付款"))),
                float(any(term in value for term in ("收到", "入账"))),
                float(any(term in value for term in ("450000", "45万元"))),
                float(any(term in value for term in ("2021年3月5日", "交易日期"))),
                0.01,
            ],
            dtype=np.float32,
        )
        features /= np.linalg.norm(features)
        return features.tolist()

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [self._vector(item) for item in texts]

    def embed_query(self, text: str) -> list[float]:
        return self._vector(text)


def setup(tmp_path: Path):
    url = f"sqlite:///{(tmp_path / 'retrieval.db').as_posix()}"
    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(ROOT / "migrations"))
    config.set_main_option("sqlalchemy.url", url)
    command.upgrade(config, "head")
    application = build_case_application(url)
    factory = build_session_factory(application.engine)
    ingestion = DefaultIngestionService(SqlAlchemyDocumentRepository(factory), tmp_path)
    retrieval = DefaultRetrievalService(SqliteChunkRepository(factory), SemanticFakeEmbedding())
    return url, application, factory, ingestion, retrieval


def import_fixture(service: DefaultIngestionService, case_id: str, path: Path):
    return service.import_document(case_id, path)


def test_modes_api_case_isolation_idempotence_and_restart(tmp_path: Path) -> None:
    url, application, factory, ingestion, retrieval = setup(tmp_path)
    case = application.service.create_case("检索合成案件")
    other = application.service.create_case("隔离案件")
    import_fixture(ingestion, case.case_id, TEXT_PDF)
    import_fixture(ingestion, case.case_id, SCAN_PDF)
    other_document = import_fixture(ingestion, other.case_id, TEXT_PDF)

    first = retrieval.index_case(case.case_id)
    assert first.pages_seen == 4
    assert first.chunks_created == 4
    assert first.embeddings_created == 4
    repeated = retrieval.index_case(case.case_id)
    assert repeated.chunks_total == 4
    assert repeated.chunks_created == 0
    assert repeated.embeddings_created == 0
    other_index = retrieval.index_case(other.case_id)
    assert other_index.chunks_created == 2

    expectations = [
        ("陈军", "陈军"),
        ("2021年3月5日", "2021年3月5日"),
        ("45万元", "450000元"),
    ]
    for query, phrase in expectations:
        hits = retrieval.search(case.case_id, query, "bm25", 3)
        assert hits and phrase in hits[0].text
        assert all(hit.document_id and hit.document_version_id and hit.page_id for hit in hits)

    semantic = retrieval.search(case.case_id, "哪笔交易未能入账", "vector", 3)
    assert semantic and "失败，未入账" in semantic[0].text
    hybrid = retrieval.search(case.case_id, "哪笔交易未能入账", "hybrid", 3)
    assert hybrid and "失败，未入账" in hybrid[0].text
    assert retrieval.search(case.case_id, "房屋租赁期限和租金", "bm25", 3) == []
    assert all(hit.document_id != other_document.document_id for hit in hybrid)

    with factory() as session:
        assert session.scalar(select(func.count()).select_from(ChunkModel)) == 6
        assert session.scalar(select(func.count()).select_from(ChunkEmbeddingModel)) == 6
        assert session.scalar(text("SELECT count(*) FROM chunk_fts")) == 6
        for chunk in session.scalars(select(ChunkModel)):
            source_page = session.get(PageModel, chunk.page_id)
            assert source_page is not None
            assert chunk.text == source_page.text[chunk.start_offset : chunk.end_offset]
            assert chunk.case_id in {case.case_id, other.case_id}

    with TestClient(create_app(application.service, ingestion, retrieval)) as client:
        indexed = client.post(f"/api/cases/{case.case_id}/index")
        assert indexed.status_code == 200
        assert indexed.json()["chunks_created"] == 0
        response = client.get(
            f"/api/cases/{case.case_id}/search",
            params={"q": "陈军", "mode": "hybrid", "limit": 2},
        )
        assert response.status_code == 200
        body = response.json()
        assert body[0]["rank"] == 1
        assert {
            "chunk_id",
            "page_id",
            "document_id",
            "document_version_id",
            "page_number",
            "text",
            "score",
        }.issubset(body[0])
        assert (
            client.get(
                f"/api/cases/{case.case_id}/search", params={"q": "x", "mode": "bad"}
            ).status_code
            == 422
        )
        assert client.get("/api/cases/missing/search", params={"q": "陈军"}).status_code == 404
    application.engine.dispose()

    restarted = build_case_application(url)
    restarted_retrieval = DefaultRetrievalService(
        SqliteChunkRepository(build_session_factory(restarted.engine)), SemanticFakeEmbedding()
    )
    hits = restarted_retrieval.search(case.case_id, "45万元", "hybrid", 3)
    assert hits and "450000元" in hits[0].text
    restarted.engine.dispose()


def test_new_version_is_searchable_and_old_chunk_reference_survives(tmp_path: Path) -> None:
    _, application, factory, ingestion, retrieval = setup(tmp_path)
    case = application.service.create_case("版本检索案件")
    original = import_fixture(ingestion, case.case_id, TEXT_PDF)
    retrieval.index_case(case.case_id)
    old_hits = retrieval.search(case.case_id, "2021年6月1日", "bm25", 3)
    assert old_hits
    old_chunk_id = old_hits[0].chunk_id
    old_page_id = old_hits[0].page_id

    with SCAN_PDF.open("rb") as source:
        updated = ingestion.upload_document(
            case.case_id, source, SCAN_PDF.name, original.document_id
        )
    indexed = retrieval.index_case(case.case_id)
    assert indexed.document_versions_seen == 2
    assert indexed.chunks_created == 2
    assert indexed.embeddings_created == 2
    current = retrieval.search(case.case_id, "陈军", "hybrid", 3)
    assert current and current[0].document_version_id == updated.document_version_id
    assert "陈军" in current[0].text

    repository = SqliteChunkRepository(factory)
    old_source = repository.hydrate(case.case_id, [old_chunk_id])[old_chunk_id]
    assert old_source.page_id == old_page_id
    assert old_source.document_version_id != updated.document_version_id
    repeated = retrieval.index_case(case.case_id)
    assert repeated.chunks_created == repeated.embeddings_created == 0
    application.engine.dispose()


def test_explicit_page_number_matches_indexed_page_metadata(tmp_path: Path) -> None:
    _, application, _, ingestion, retrieval = setup(tmp_path)
    case = application.service.create_case("页码检索合成案件")
    import_fixture(ingestion, case.case_id, TEXT_PDF)
    retrieval.index_case(case.case_id)
    page_two = retrieval.search(case.case_id, "第2页的日期是什么", "bm25", 2)
    assert page_two and page_two[0].page_number == 2
    assert page_two[0].text
    application.engine.dispose()


def test_reindex_refreshes_legacy_fts_tokens_without_rewriting_chunks(tmp_path: Path) -> None:
    _, application, factory, ingestion, retrieval = setup(tmp_path)
    case = application.service.create_case("索引版本迁移合成案件")
    import_fixture(ingestion, case.case_id, TEXT_PDF)
    retrieval.index_case(case.case_id)
    with factory.begin() as session:
        session.execute(text("UPDATE chunk_fts SET tokenization_version = 'old-tokenizer'"))
    refreshed = retrieval.index_case(case.case_id)
    assert refreshed.chunks_created == 0
    assert refreshed.embeddings_created == 0
    hits = retrieval.search(case.case_id, "第2页", "bm25", 2)
    assert hits and hits[0].page_number == 2
    application.engine.dispose()
