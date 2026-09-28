"""Product startup, safe diagnostics, local workflows and shutdown boundaries."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any
from unittest.mock import Mock

import httpx
import numpy as np
import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from typer.testing import CliRunner

from casefile_ai.application.system_contracts import SystemStatus
from casefile_ai.config import Settings
from casefile_ai.infrastructure.llm.local_embeddings import (
    LocalSentenceTransformerEmbeddingProvider,
)
from casefile_ai.infrastructure.llm.openai_compatible import OpenAICompatibleLLMProvider
from casefile_ai.infrastructure.safe_logging import SafeLogging, safe_event
from casefile_ai.interfaces.api.main import create_app
from casefile_ai.interfaces.cli.main import app as cli

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "tests" / "fixtures" / "synthetic_case"
SECRET = "synthetic-secret-never-log-7821"
PRIVATE = "合成检索词-付款人身份-7821"


class FixtureEncoder:
    def encode(self, sentences: list[str], **_options: Any) -> Any:
        return np.asarray(
            [[1.0, float("王海" in item), 0.1] for item in sentences], dtype=np.float32
        )


@pytest.fixture
def settings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Settings:
    # Tests must not inherit the operator's real .env or model authorization.
    monkeypatch.setenv("CASEFILE_APP_HOME", str(tmp_path))
    for name in ("BASE_URL", "API_KEY", "MODEL", "ENABLED"):
        monkeypatch.delenv("CASEFILE_LLM_" + name, raising=False)
    return Settings(_env_file=None, app_home=tmp_path)


@pytest.fixture
def local_encoder(monkeypatch: pytest.MonkeyPatch) -> None:
    def build(**values: Any) -> LocalSentenceTransformerEmbeddingProvider:
        return LocalSentenceTransformerEmbeddingProvider(**values, encoder=FixtureEncoder())

    monkeypatch.setattr(
        "casefile_ai.application.bootstrap.LocalSentenceTransformerEmbeddingProvider", build
    )


def test_no_cloud_full_local_workflow_and_safe_model_errors(
    settings: Settings,
    local_encoder: None,
) -> None:
    with TestClient(create_app(settings=settings)) as client:
        assert client.get("/").status_code == 200
        case = client.post("/api/cases", json={"name": "合成本地案件"}).json()["case_id"]
        document_ids = []
        for path in FIXTURES.glob("*.pdf"):
            response = client.post(
                f"/api/cases/{case}/documents",
                files={"file": (path.name, path.read_bytes(), "application/pdf")},
            )
            assert response.status_code == 200, response.text
            document_ids.append(response.json()["document_id"])
        assert len(document_ids) == 2
        assert client.post(f"/api/cases/{case}/index").status_code == 200
        assert client.get(f"/api/cases/{case}/search", params={"q": "王海"}).json()
        assert client.get(f"/api/documents/{document_ids[0]}/original").status_code == 200
        for endpoint in ("ask", "answers/unused/claims"):
            response = client.post(f"/api/cases/{case}/{endpoint}", json={"question": "金额？"})
            assert response.status_code == 503
            assert response.json()["error"]["category"] == "model_not_configured"
        # Reading persisted analysis and deterministic/no-candidate detection is local.
        for endpoint in ("answers", "claims", "conflict-candidates", "contradictions", "timeline"):
            assert client.get(f"/api/cases/{case}/{endpoint}").status_code == 200
        assert client.post(f"/api/cases/{case}/contradictions").json() == []


def test_status_schema_and_no_loading_no_network(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings.llm_base_url = "https://model.invalid/v1"
    settings.llm_api_key = SECRET
    settings.llm_model = "fixture-model"
    settings.llm_enabled = True
    network = Mock(side_effect=AssertionError("status must not call a model"))
    loading = Mock(side_effect=AssertionError("status must not initialize/download models"))
    monkeypatch.setattr(OpenAICompatibleLLMProvider, "_post_chat", network)
    monkeypatch.setattr(LocalSentenceTransformerEmbeddingProvider, "_get_encoder", loading)
    monkeypatch.setattr(
        "casefile_ai.infrastructure.parsers.pdf.RapidOcrPageReader._get_engine", loading
    )
    with TestClient(create_app(settings=settings)) as client:
        for _ in range(2):
            assert client.get("/health").json() == {"status": "ok"}
            response = client.get("/api/system/status")
            assert response.status_code == 200
            parsed = SystemStatus.model_validate(response.json())
            assert parsed.database.schema_status == "current"
            assert parsed.database.reachable
            assert parsed.qa_provider.configured and parsed.qa_provider.enabled
            assert not parsed.qa_provider.connection_verified
            assert not parsed.embedding.loaded
            assert SECRET not in response.text
            assert "Authorization" not in response.text
            assert "llm_base_url" not in response.text
            assert set(response.json()) == {
                "status",
                "configuration_file",
                "application",
                "database",
                "data_directory",
                "embedding",
                "ocr",
                "qa_provider",
                "logs",
                "notices",
            }
        assert set(parsed.qa_provider.model_dump()) == {
            "configured",
            "enabled",
            "connection_verified",
        }
        bad = response.json()
        bad["qa_provider"]["api_key"] = SECRET
        with pytest.raises(ValidationError):
            SystemStatus.model_validate(bad)
    network.assert_not_called()
    loading.assert_not_called()


def test_configured_cloud_requires_explicit_enable(settings: Settings) -> None:
    settings.llm_base_url = "https://model.invalid/v1"
    settings.llm_api_key = SECRET
    settings.llm_model = "fixture"
    with TestClient(create_app(settings=settings)) as client:
        case = client.post("/api/cases", json={"name": "合成"}).json()["case_id"]
        response = client.post(f"/api/cases/{case}/ask", json={"question": "金额？"})
        assert response.json()["error"]["category"] == "model_disabled"
        assert not client.app.state.runtime.diagnostics.snapshot().qa_provider.enabled


def test_database_failure_and_unwritable_directory_are_degraded(
    settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with TestClient(create_app(settings=settings)) as client:
        runtime = client.app.state.runtime
        monkeypatch.setattr(runtime.cases.engine, "connect", Mock(side_effect=OSError(SECRET)))
        response = client.get("/api/system/status")
        assert response.json()["status"] == "degraded"
        assert not response.json()["database"]["reachable"]
        assert response.json()["database"]["schema_status"] == "unavailable"
        assert SECRET not in response.text
        monkeypatch.setattr(
            "casefile_ai.application.services.system_status.tempfile.TemporaryFile",
            Mock(side_effect=PermissionError(SECRET)),
        )
        response = client.get("/api/system/status")
        assert not response.json()["data_directory"]["writable"]
        assert response.json()["data_directory"]["exists"]
        assert response.json()["status"] == "degraded"


def test_cached_unloaded_is_available(settings: Settings, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "casefile_ai.application.services.system_status.embedding_cached", lambda _: True
    )
    monkeypatch.setattr(
        "casefile_ai.application.services.system_status.dependencies_available", lambda *args: True
    )
    monkeypatch.setattr(
        "casefile_ai.application.services.system_status.ocr_models_available", lambda: True
    )
    with TestClient(create_app(settings=settings)) as client:
        data = client.get("/api/system/status").json()
        assert data["embedding"]["available"] and not data["embedding"]["loaded"]
        assert data["ocr"]["model_available"]
        assert data["status"] == "ready"


def test_lifespan_closes_http_encoder_and_engine(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings.llm_base_url = "https://model.invalid/v1"
    settings.llm_api_key = SECRET
    settings.llm_model = "fixture"
    settings.llm_enabled = True
    with TestClient(create_app(settings=settings)) as client:
        runtime = client.app.state.runtime
        assert isinstance(runtime.llm, OpenAICompatibleLLMProvider)
        http_client = runtime.llm._client
        dispose = Mock(wraps=runtime.cases.engine.dispose)
        release = Mock(wraps=runtime.embeddings.close)
        monkeypatch.setattr(runtime.cases.engine, "dispose", dispose)
        monkeypatch.setattr(runtime.embeddings, "close", release)
        assert not http_client.is_closed
    assert http_client.is_closed
    dispose.assert_called_once()
    release.assert_called_once()


def test_secret_query_exception_and_sql_parameters_never_logged(
    settings: Settings, local_encoder: None
) -> None:
    settings.llm_api_key = SECRET
    with TestClient(create_app(settings=settings)) as client:
        case = client.post("/api/cases", json={"name": "合成"}).json()["case_id"]
        client.get(f"/api/cases/{case}/search", params={"q": PRIVATE, "mode": "bm25"})
        logging.getLogger("uvicorn.access").info("GET /search?q=%s", PRIVATE)
        logging.getLogger("httpx").info("Authorization: Bearer %s", SECRET)
        try:
            raise ValueError(f"SQL parameters={SECRET}; evidence={PRIVATE}")
        except ValueError:
            logging.getLogger("external").error("provider body %s", SECRET, exc_info=True)
        safe_event("provider", "authentication_error", level=logging.ERROR)
    texts = "\n".join(path.read_text(encoding="utf-8") for path in settings.log_dir.glob("*.log"))
    assert SECRET not in texts and PRIVATE not in texts
    assert "Authorization" not in texts and "parameters=" not in texts
    assert "q=" not in texts and "Traceback" not in texts
    for line in texts.splitlines():
        if not line:
            continue
        assert set(json.loads(line)).issubset(
            {
                "timestamp",
                "level",
                "operation",
                "category",
                "diagnostic_id",
                "duration_ms",
                "case_id",
                "document_id",
            }
        )
    assert "authentication_error" in texts


def test_failed_log_directory_falls_back_without_losing_gui(settings: Settings) -> None:
    settings.log_dir.write_text("not a directory", encoding="utf-8")
    with TestClient(create_app(settings=settings)) as client:
        assert client.get("/").status_code == 200
        assert not client.get("/api/system/status").json()["logs"]["file_logging_available"]


def test_rotation_is_bounded_utf8_and_logging_restored(tmp_path: Path) -> None:
    original_factory = logging.getLogRecordFactory()
    manager = SafeLogging(tmp_path, "INFO")
    try:
        for handler in manager._handlers:
            if hasattr(handler, "maxBytes"):
                handler.maxBytes = 350
        for _ in range(20):
            safe_event("startup")
    finally:
        manager.close()
    assert logging.getLogRecordFactory() is original_factory
    assert len(list(tmp_path.glob("app.log*"))) <= 4
    for path in tmp_path.glob("*.log*"):
        path.read_text(encoding="utf-8")


def test_cli_non_cwd_startup_and_no_access_logging(
    settings: Settings,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    other = tmp_path / "other"
    other.mkdir()
    monkeypatch.chdir(other)
    monkeypatch.setattr("casefile_ai.interfaces.cli.main.Settings", lambda: settings)

    def run(application: Any, **options: Any) -> None:
        assert options["access_log"] is False
        assert options["log_config"] is None
        with TestClient(application) as client:
            assert client.get("/api/system/status").json()["database"]["schema_status"] == "current"

    monkeypatch.setattr("casefile_ai.interfaces.cli.main.uvicorn.run", run)
    result = CliRunner().invoke(cli, ["serve"])
    assert result.exit_code == 0, result.output
    assert "模型服务：未配置" in result.output
    assert str(settings.data_dir) in result.output
    assert SECRET not in result.output
    assert not (other / "data").exists()


@pytest.mark.parametrize(
    ("upstream_status", "category"),
    [
        (401, "authentication_error"),
        (403, "permission_error"),
        (429, "rate_limited"),
        (503, "unavailable"),
    ],
)
def test_provider_categories_survive_qa_wrapper_without_body_leak(
    settings: Settings,
    local_encoder: None,
    upstream_status: int,
    category: str,
) -> None:
    settings.llm_base_url = "https://model.invalid/v1"
    settings.llm_api_key = SECRET
    settings.llm_model = "fixture"
    settings.llm_enabled = True
    with TestClient(create_app(settings=settings)) as client:
        runtime = client.app.state.runtime
        runtime.llm._client.close()
        runtime.llm._client = httpx.Client(
            transport=httpx.MockTransport(
                lambda _: httpx.Response(upstream_status, text=SECRET + PRIVATE),
            )
        )
        case = client.post("/api/cases", json={"name": "合成错误边界"}).json()["case_id"]
        path = next(FIXTURES.glob("01*.pdf"))
        client.post(f"/api/cases/{case}/documents", files={"file": (path.name, path.read_bytes())})
        client.post(f"/api/cases/{case}/index")
        response = client.post(f"/api/cases/{case}/ask", json={"question": "王海是谁？"})
        assert response.status_code == 503
        assert response.json()["error"]["category"] == category
        assert response.json()["error"]["http_status"] == upstream_status
        assert SECRET not in response.text and PRIVATE not in response.text
    text = (settings.log_dir / "error.log").read_text(encoding="utf-8")
    assert category in text and SECRET not in text and PRIVATE not in text


def test_schema_status_detects_missing_tables_and_outdated_revision(settings: Settings) -> None:
    with TestClient(create_app(settings=settings)) as client:
        runtime = client.app.state.runtime
        head = client.get("/api/system/status").json()["database"]["expected_revision"]
        with runtime.cases.engine.begin() as connection:
            connection.exec_driver_sql("UPDATE alembic_version SET version_num='20260922_0001'")
        assert client.get("/api/system/status").json()["database"]["schema_status"] == "outdated"
        with runtime.cases.engine.begin() as connection:
            connection.exec_driver_sql("UPDATE alembic_version SET version_num = ?", (head,))
            connection.exec_driver_sql("DROP TABLE chunk_fts")
        response = client.get("/api/system/status").json()
        assert response["database"]["schema_status"] == "missing"
        assert response["status"] == "degraded"


def test_failed_storage_still_exposes_diagnostics_from_factory(settings: Settings) -> None:
    settings.data_dir.write_text("blocked path", encoding="utf-8")
    with TestClient(create_app(settings=settings)) as client:
        assert client.get("/").status_code == 200
        data = client.get("/api/system/status").json()
        assert data["status"] == "degraded"
        assert not data["database"]["reachable"]
        assert not data["data_directory"]["writable"]


def test_cli_preflight_failure_is_safe(settings: Settings, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("casefile_ai.interfaces.cli.main.Settings", lambda: settings)
    monkeypatch.setattr(
        "casefile_ai.interfaces.cli.main._prepare", Mock(side_effect=OSError(SECRET))
    )
    run = Mock()
    monkeypatch.setattr("casefile_ai.interfaces.cli.main.uvicorn.run", run)
    result = CliRunner().invoke(cli, ["serve"])
    assert result.exit_code == 1
    assert "启动检查失败" in result.output and SECRET not in result.output
    run.assert_not_called()


def test_previously_disabled_loggers_are_enabled_and_restored(tmp_path: Path) -> None:
    logger = logging.getLogger("casefile_ai.safe")
    old_disabled = logger.disabled
    logger.disabled = True
    manager = SafeLogging(tmp_path, "INFO")
    try:
        safe_event("startup")
        assert not logger.disabled
    finally:
        manager.close()
    assert logger.disabled
    logger.disabled = old_disabled
    assert '"operation": "startup"' in (tmp_path / "app.log").read_text(encoding="utf-8")


def test_shutdown_releases_remaining_resources_after_close_error(
    settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with TestClient(create_app(settings=settings)) as client:
        runtime = client.app.state.runtime
        monkeypatch.setattr(runtime.llm, "close", Mock(side_effect=OSError(SECRET)))
        dispose = Mock(wraps=runtime.cases.engine.dispose)
        monkeypatch.setattr(runtime.cases.engine, "dispose", dispose)
    dispose.assert_called_once()
    text = (settings.log_dir / "error.log").read_text(encoding="utf-8")
    assert SECRET not in text


def test_legacy_data_notice_does_not_move_files(
    settings: Settings,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    legacy = tmp_path / "legacy-project" / "data"
    legacy.mkdir(parents=True)
    marker = legacy / "casefile-ai.db"
    marker.write_bytes(b"synthetic legacy marker")
    monkeypatch.setattr("casefile_ai.application.services.system_status.SOURCE_ROOT", legacy.parent)
    with TestClient(create_app(settings=settings)) as client:
        notices = client.get("/api/system/status").json()["notices"]
        assert any("未自动迁移" in message for message in notices)
        assert marker.read_bytes() == b"synthetic legacy marker"
        assert settings.database_path != marker
