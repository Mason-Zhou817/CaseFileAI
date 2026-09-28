from pathlib import Path

from fastapi.testclient import TestClient

from casefile_ai.application.services.cases import DefaultCaseService
from casefile_ai.infrastructure.db.repositories import SqlAlchemyCaseRepository
from casefile_ai.infrastructure.db.session import (
    build_engine,
    build_session_factory,
    initialize_schema,
)
from casefile_ai.interfaces.api.main import create_app


def _client(tmp_path: Path) -> tuple[TestClient, object]:
    engine = build_engine(f"sqlite:///{(tmp_path / 'api.db').as_posix()}")
    initialize_schema(engine)
    service = DefaultCaseService(SqlAlchemyCaseRepository(build_session_factory(engine)))
    return TestClient(create_app(service)), engine


def test_case_api_create_list_and_get(tmp_path: Path) -> None:
    client, engine = _client(tmp_path)

    created = client.post(
        "/api/cases", json={"name": "  星河合成案件  ", "description": "测试"}
    )
    assert created.status_code == 201
    body = created.json()
    assert body["name"] == "星河合成案件"

    listed = client.get("/api/cases")
    assert listed.status_code == 200
    assert listed.json()["cases"] == [body]

    fetched = client.get(f"/api/cases/{body['case_id']}")
    assert fetched.status_code == 200
    assert fetched.json() == body
    engine.dispose()  # type: ignore[attr-defined]


def test_case_api_rejects_blank_and_returns_not_found(tmp_path: Path) -> None:
    client, engine = _client(tmp_path)

    assert client.post("/api/cases", json={"name": "   "}).status_code == 422
    missing = client.get("/api/cases/missing")
    assert missing.status_code == 404
    assert "missing" in missing.json()["detail"]
    engine.dispose()  # type: ignore[attr-defined]
