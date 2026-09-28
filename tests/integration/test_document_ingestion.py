"""Real fixture ingestion through migrated SQLite and HTTP."""

import hashlib
from concurrent.futures import ThreadPoolExecutor
from io import BytesIO
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import event, func, inspect, select

from casefile_ai.application.bootstrap import build_case_application
from casefile_ai.application.services.documents import DefaultIngestionService
from casefile_ai.infrastructure.db.documents import SqlAlchemyDocumentRepository
from casefile_ai.infrastructure.db.models import DocumentModel, DocumentVersionModel, PageModel
from casefile_ai.infrastructure.db.session import build_session_factory
from casefile_ai.infrastructure.parsers.pdf import ExtractedPage
from casefile_ai.interfaces.api.main import create_app

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "tests/fixtures/synthetic_case"
TEXT = FIXTURES / "01_询问笔录_王海.pdf"
SCAN = FIXTURES / "02_银行凭证_扫描件.pdf"


def setup(tmp_path: Path) -> tuple[str, Config]:
    url = f"sqlite:///{(tmp_path / 'ingestion.db').as_posix()}"
    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(ROOT / "migrations"))
    config.set_main_option("sqlalchemy.url", url)
    command.upgrade(config, "head")
    return url, config


def test_real_text_scan_versions_and_restart(tmp_path: Path) -> None:
    url, config = setup(tmp_path)
    application = build_case_application(url)
    factory = build_session_factory(application.engine)
    service = DefaultIngestionService(SqlAlchemyDocumentRepository(factory), tmp_path)
    with TestClient(create_app(application.service, service)) as client:
        case = client.post("/api/cases", json={"name": "合成文档案件"}).json()["case_id"]
        endpoint = f"/api/cases/{case}/documents"
        native = TEXT.read_bytes()
        response = client.post(endpoint, files={"file": (TEXT.name, native, "application/pdf")})
        assert response.status_code == 200, response.text
        first = response.json()
        assert first["content_sha256"] == hashlib.sha256(native).hexdigest()
        assert first["page_count"] == 2
        document = first["document_id"]
        base = f"/api/documents/{document}"
        original_pages = []
        for number, phrase in [(1, "2021年6月1日"), (2, "付款人是王海本人")]:
            page = client.get(f"{base}/pages/{number}").json()
            assert page["case_id"] == case
            assert page["document_id"] == document
            assert page["document_version_id"] == first["document_version_id"]
            assert page["page_number"] == number
            assert phrase in page["text"]
            assert page["text_hash"] == hashlib.sha256(page["text"].encode()).hexdigest()
            assert not page["ocr_used"]
            original_pages.append(page)
        assert client.get(f"{base}/original").content == native
        duplicate = client.post(endpoint, files={"file": ("renamed.pdf", native)}).json()
        assert duplicate["duplicate"] is True
        assert duplicate["document_version_id"] == first["document_version_id"]
        assert len(client.get(endpoint).json()) == 1

        scan_bytes = SCAN.read_bytes()
        updated = client.post(
            endpoint, data={"document_id": document}, files={"file": (SCAN.name, scan_bytes)}
        )
        assert updated.status_code == 200, updated.text
        second = updated.json()
        assert second["document_id"] == document
        assert second["document_version_id"] != first["document_version_id"]
        assert second["version_number"] == 2
        for number, phrase in [(1, "450000元"), (2, "未收到王海支付")]:
            page = client.get(f"{base}/pages/{number}").json()
            assert page["ocr_used"] is True
            assert phrase in page["text"]
            assert page["page_id"] != original_pages[number - 1]["page_id"]
        assert client.get(f"{base}/original").content == scan_bytes
        history = {"document_version_id": first["document_version_id"]}
        assert client.get(f"{base}/original", params=history).content == native
        assert client.get(f"{base}/pages/1", params=history).json() == original_pages[0]
        assert len(client.get(base).json()["versions"]) == 2
        old_again = client.post(
            endpoint, data={"document_id": document}, files={"file": (TEXT.name, native)}
        ).json()
        assert old_again["duplicate"] and old_again["version_number"] == 1
        assert client.get(base).json()["latest"]["version_number"] == 2
        assert client.get(f"{base}/pages/0").status_code == 422
        assert client.get(f"{base}/pages/3").status_code == 404
        assert (
            client.get(f"{base}/pages/1", params={"document_version_id": "missing"}).status_code
            == 404
        )

        other_case = client.post("/api/cases", json={"name": "另案"}).json()["case_id"]
        rejected = client.post(
            f"/api/cases/{other_case}/documents",
            data={"document_id": document},
            files={"file": (TEXT.name, native)},
        )
        assert rejected.status_code == 404
        assert len(list((tmp_path / "originals").glob("*.pdf"))) == 2
        assert not list((tmp_path / "originals").glob("*.upload"))
    application.engine.dispose()

    restarted = build_case_application(url)
    service = DefaultIngestionService(
        SqlAlchemyDocumentRepository(build_session_factory(restarted.engine)), tmp_path
    )
    with TestClient(create_app(restarted.service, service)) as client:
        assert client.get(f"{base}/pages/1", params=history).json() == original_pages[0]
        assert client.get(f"{base}/original", params=history).content == native
        assert (
            client.get(base).json()["latest"]["document_version_id"]
            == second["document_version_id"]
        )
    restarted.engine.dispose()
    command.downgrade(config, "20260922_0001")
    check = build_case_application(url)
    assert "cases" in inspect(check.engine).get_table_names()
    assert "documents" not in inspect(check.engine).get_table_names()
    check.engine.dispose()


def test_invalid_pdf_and_parser_failure_leave_no_rows_or_files(tmp_path: Path) -> None:
    url, _ = setup(tmp_path)
    application = build_case_application(url)
    case = application.service.create_case("失败测试")
    factory = build_session_factory(application.engine)
    service = DefaultIngestionService(SqlAlchemyDocumentRepository(factory), tmp_path)
    with TestClient(create_app(application.service, service)) as client:
        endpoint = f"/api/cases/{case.case_id}/documents"
        for name, content in [("x.pdf", b"not a PDF"), ("empty.pdf", b""), ("x.txt", b"text")]:
            assert client.post(endpoint, files={"file": (name, content)}).status_code == 422
        assert (
            client.post(
                "/api/cases/missing/documents", files={"file": (TEXT.name, TEXT.read_bytes())}
            ).status_code
            == 404
        )

    def fail_parser(path: Path) -> list[ExtractedPage]:
        raise RuntimeError("synthetic parser failure")

    failing = DefaultIngestionService(SqlAlchemyDocumentRepository(factory), tmp_path, fail_parser)
    with pytest.raises(RuntimeError, match="synthetic parser failure"):
        failing.import_document(case.case_id, TEXT)
    with factory() as session:
        for model in (DocumentModel, DocumentVersionModel, PageModel):
            assert session.scalar(select(func.count()).select_from(model)) == 0
    assert not list((tmp_path / "originals").iterdir())
    application.engine.dispose()


def test_database_failure_rolls_back_bundle_and_removes_original(tmp_path: Path) -> None:
    url, _ = setup(tmp_path)
    application = build_case_application(url)
    case = application.service.create_case("写入失败测试")
    factory = build_session_factory(application.engine)
    service = DefaultIngestionService(SqlAlchemyDocumentRepository(factory), tmp_path)

    def fail_insert(*args: object) -> None:
        raise RuntimeError("synthetic write failure")

    event.listen(PageModel, "before_insert", fail_insert)
    try:
        with pytest.raises(RuntimeError, match="synthetic write failure"):
            service.import_document(case.case_id, TEXT)
    finally:
        event.remove(PageModel, "before_insert", fail_insert)
    with factory() as session:
        for model in (DocumentModel, DocumentVersionModel, PageModel):
            assert session.scalar(select(func.count()).select_from(model)) == 0
    assert not list((tmp_path / "originals").iterdir())
    application.engine.dispose()


def test_dedup_is_case_scoped_and_filename_is_not_identity(tmp_path: Path) -> None:
    url, _ = setup(tmp_path)
    application = build_case_application(url)
    first_case = application.service.create_case("案件甲")
    second_case = application.service.create_case("案件乙")
    service = DefaultIngestionService(
        SqlAlchemyDocumentRepository(build_session_factory(application.engine)),
        tmp_path,
    )
    original = TEXT.read_bytes()
    first = service.upload_document(first_case.case_id, BytesIO(original), "same.pdf")
    second = service.upload_document(second_case.case_id, BytesIO(original), "same.pdf")
    assert first.document_id != second.document_id
    # PDF trailing whitespace changes file hash without creating another authored PDF.
    changed = service.upload_document(first_case.case_id, BytesIO(original + b"\n"), "same.pdf")
    assert changed.document_id != first.document_id
    assert changed.version_number == 1
    assert len(service.list_documents(first_case.case_id)) == 2
    application.engine.dispose()


def test_concurrent_duplicate_imports_are_atomic(tmp_path: Path) -> None:
    url, _ = setup(tmp_path)
    application = build_case_application(url)
    case = application.service.create_case("并发测试")
    factory = build_session_factory(application.engine)
    service = DefaultIngestionService(SqlAlchemyDocumentRepository(factory), tmp_path)
    content = TEXT.read_bytes()

    def upload(_: int):
        return service.upload_document(case.case_id, BytesIO(content), TEXT.name)

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(upload, range(2)))
    assert len({result.document_version_id for result in results}) == 1
    assert sum(result.duplicate for result in results) == 1
    with factory() as session:
        assert session.scalar(select(func.count()).select_from(DocumentModel)) == 1
        assert session.scalar(select(func.count()).select_from(DocumentVersionModel)) == 1
        assert session.scalar(select(func.count()).select_from(PageModel)) == 2
    assert len(list((tmp_path / "originals").glob("*.pdf"))) == 1
    application.engine.dispose()
