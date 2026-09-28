"""Material states, immutable history, processing and safe retry through real persistence."""

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event
from typing import Any
from unittest.mock import Mock

import numpy as np
import pytest
from fastapi.testclient import TestClient

from casefile_ai.application.material_contracts import IndexInProgressError
from casefile_ai.application.services.materials import MaterialsService
from casefile_ai.config import Settings
from casefile_ai.infrastructure.db.materials import SqlAlchemyMaterialRepository
from casefile_ai.infrastructure.db.session import build_session_factory
from casefile_ai.infrastructure.llm.local_embeddings import (
    LocalSentenceTransformerEmbeddingProvider,
)
from casefile_ai.interfaces.api.main import create_app

ROOT = Path(__file__).resolve().parents[2]
PDF = next((ROOT / "tests/fixtures/synthetic_case").glob("01*.pdf"))
OTHER_PDF = next((ROOT / "tests/fixtures/synthetic_case").glob("02*.pdf"))


class Encoder:
    def encode(self, sentences: list[str], **_kwargs: Any) -> Any:
        return np.asarray([[1.0, 0.5, 0.1] for _ in sentences], dtype=np.float32)


@pytest.fixture
def configured(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Settings:
    def build(**values: Any) -> LocalSentenceTransformerEmbeddingProvider:
        return LocalSentenceTransformerEmbeddingProvider(**values, encoder=Encoder())

    monkeypatch.setattr(
        "casefile_ai.application.bootstrap.LocalSentenceTransformerEmbeddingProvider", build
    )
    return Settings(
        _env_file=None,
        app_home=tmp_path,
        data_dir=tmp_path / "data",
        database_url=f"sqlite:///{(tmp_path / 'data' / 'case.db').as_posix()}",
        log_dir=tmp_path / "logs",
        llm_base_url=None,
        llm_api_key=None,
        llm_model=None,
        llm_enabled=False,
    )


def add_case(client: TestClient) -> str:
    return str(client.post("/api/cases", json={"name": "合成材料状态案件"}).json()["case_id"])


def upload(client: TestClient, case: str, path: Path = PDF, document: str | None = None):
    response = client.post(
        f"/api/cases/{case}/documents",
        files={"file": (path.name, path.read_bytes())},
        data={"document_id": document} if document else {},
    )
    assert response.status_code == 200
    return response.json()


def snapshot(client: TestClient, case: str):
    response = client.get(f"/api/cases/{case}/materials")
    assert response.status_code == 200
    return response.json()


def test_import_ready_history_and_latest_version_invalidation(configured: Settings) -> None:
    with TestClient(create_app(settings=configured)) as client:
        case = add_case(client)
        assert snapshot(client, case)["documents"] == []
        version = upload(client, case)
        state = snapshot(client, case)
        assert state["status"] == "not_indexed" and state["action_label"] == "建立索引"
        assert state["documents"][0]["version"] == version
        original_parser = client.app.state.runtime.ingestion._parser
        parser = Mock(side_effect=AssertionError("index must not rerun OCR"))
        client.app.state.runtime.ingestion._parser = parser
        assert client.post(f"/api/cases/{case}/index").status_code == 200
        assert snapshot(client, case)["status"] == "ready"
        parser.assert_not_called()
        client.app.state.runtime.ingestion._parser = original_parser
        newer = upload(client, case, OTHER_PDF, version["document_id"])
        assert newer["version_number"] == 2
        assert snapshot(client, case)["status"] == "not_indexed"
        history = client.get(f"/api/documents/{version['document_id']}").json()
        assert [v["version_number"] for v in history["versions"]] == [2, 1]
        assert "created_at" not in history["versions"][0]
        for v, path in ((version, PDF), (newer, OTHER_PDF)):
            original = client.get(
                f"/api/documents/{v['document_id']}/original",
                params={
                    "document_version_id": v["document_version_id"],
                },
            )
            assert original.content == path.read_bytes()
        assert client.post(f"/api/cases/{case}/index").status_code == 200
        assert snapshot(client, case)["status"] == "ready"


@pytest.mark.parametrize("part", ["embedding", "fts", "chunking", "partial"])
def test_existing_index_requires_current_versions_and_completeness(
    configured: Settings,
    part: str,
) -> None:
    with TestClient(create_app(settings=configured)) as client:
        case = add_case(client)
        upload(client, case)
        client.post(f"/api/cases/{case}/index")
        statement = {
            "embedding": "UPDATE chunk_embeddings SET embedding_version = 'old-embedding'",
            "fts": "UPDATE chunk_fts SET tokenization_version = 'old-tokenizer'",
            "chunking": "UPDATE chunks SET chunking_version = 'old-chunker'",
            "partial": "DELETE FROM chunk_embeddings",
        }[part]
        with client.app.state.runtime.cases.engine.begin() as connection:
            connection.exec_driver_sql(statement)
        state = snapshot(client, case)
        assert state["status"] == "stale" and state["action_label"] == "更新索引"
        assert state["incomplete_reasons"] == ["stale"]
        if part != "chunking":
            assert client.post(f"/api/cases/{case}/index").status_code == 200
            assert snapshot(client, case)["status"] == "ready"


def test_failed_attempt_survives_restart_without_error_body_and_recovers(
    configured: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with TestClient(create_app(settings=configured)) as client:
        case = add_case(client)
        version = upload(client, case)
        runtime = client.app.state.runtime
        with monkeypatch.context() as scoped:
            scoped.setattr(
                runtime.embeddings,
                "embed_documents",
                Mock(
                    side_effect=RuntimeError(
                        "secret synthetic API_KEY prompt evidence traceback must not persist"
                    )
                ),
            )
            response = client.post(f"/api/cases/{case}/index")
        assert response.status_code == 500
        assert response.json()["error"]["category"] == "index_failed"
        state = snapshot(client, case)
        assert state["status"] == "failed"
        assert state["documents"][0]["diagnostic_id"] == response.json()["error"]["diagnostic_id"]
        assert "API_KEY" not in response.text and "API_KEY" not in str(state)
        assert client.get(f"/api/documents/{version['document_id']}/original").status_code == 200
        with runtime.cases.engine.connect() as connection:
            records = connection.exec_driver_sql("SELECT * FROM material_index_attempts").all()
            assert "API_KEY" not in str(records)
    with TestClient(create_app(settings=configured)) as client:
        assert snapshot(client, case)["status"] == "failed"
        assert client.post(f"/api/cases/{case}/index").status_code == 200
        assert snapshot(client, case)["status"] == "ready"
    assert "API_KEY" not in (configured.log_dir / "error.log").read_text(encoding="utf-8")


def test_processing_state_and_duplicate_index_guard(
    configured: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with TestClient(create_app(settings=configured)) as client:
        case = add_case(client)
        upload(client, case)
        runtime = client.app.state.runtime
        entered, release = Event(), Event()
        real_embed = runtime.embeddings.embed_documents

        def blocked(texts: list[str]) -> list[list[float]]:
            entered.set()
            assert release.wait(10)
            return real_embed(texts)

        monkeypatch.setattr(runtime.embeddings, "embed_documents", blocked)
        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(runtime.retrieval.index_case, case)
            try:
                assert entered.wait(10)
                assert snapshot(client, case)["status"] == "processing"
                with pytest.raises(IndexInProgressError):
                    runtime.retrieval.index_case(case)
                response = client.post(f"/api/cases/{case}/index")
                assert response.status_code == 409
                assert response.json()["error"]["category"] == "index_processing"
            finally:
                release.set()
            future.result(timeout=10)
        assert snapshot(client, case)["status"] == "ready"


def test_empty_text_has_honest_import_and_index_states(configured: Settings) -> None:
    with TestClient(create_app(settings=configured)) as client:
        case = add_case(client)
        upload(client, case)
        runtime = client.app.state.runtime
        with runtime.cases.engine.begin() as connection:
            connection.exec_driver_sql("UPDATE pages SET text = ''")
        assert snapshot(client, case)["status"] == "not_indexed"
        assert client.post(f"/api/cases/{case}/index").status_code == 200
        assert snapshot(client, case)["status"] == "ready"
        different = MaterialsService(
            SqlAlchemyMaterialRepository(build_session_factory(runtime.cases.engine)), "new-model"
        )
        assert different.snapshot(case).status == "stale"


def test_unknown_case_and_status_isolation(configured: Settings) -> None:
    with TestClient(create_app(settings=configured)) as client:
        a, b = add_case(client), add_case(client)
        upload(client, a)
        client.post(f"/api/cases/{a}/index")
        assert snapshot(client, a)["status"] == "ready"
        assert snapshot(client, b)["documents"] == []
        assert client.get("/api/cases/missing/materials").status_code == 404


@pytest.mark.parametrize("reason", ["not_indexed", "stale", "failed"])
def test_qa_index_incomplete_retains_contract_and_materials_supply_specific_reason(
    configured: Settings,
    monkeypatch: pytest.MonkeyPatch,
    reason: str,
) -> None:
    configured.llm_base_url = "https://fixture.invalid/v1"
    configured.llm_api_key = "synthetic-test-key"
    configured.llm_model = "fixture"
    configured.llm_enabled = True
    with TestClient(create_app(settings=configured)) as client:
        runtime = client.app.state.runtime
        network = Mock(side_effect=AssertionError("incomplete indexes must not call a model"))
        monkeypatch.setattr(runtime.llm, "generate_structured", network)
        case = add_case(client)
        upload(client, case)
        if reason == "stale":
            client.post(f"/api/cases/{case}/index")
            with runtime.cases.engine.begin() as connection:
                connection.exec_driver_sql("DELETE FROM chunk_embeddings")
        if reason == "failed":
            monkeypatch.setattr(
                runtime.embeddings,
                "embed_documents",
                Mock(side_effect=RuntimeError("synthetic index failure")),
            )
            client.post(f"/api/cases/{case}/index")
        response = client.post(f"/api/cases/{case}/ask", json={"question": "付款金额？"})
        assert response.status_code == 200
        assert response.json()["metadata"]["refusal_reason"] == "index_incomplete"
        assert response.json()["status"] == "insufficient_evidence"
        assert snapshot(client, case)["incomplete_reasons"] == [reason]
        network.assert_not_called()
