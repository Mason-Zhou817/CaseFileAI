"""Verify the HTTP/service/repository chain against a migrated persistent database."""

from pathlib import Path
from uuid import UUID

from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient

from casefile_ai.application.bootstrap import build_case_application
from casefile_ai.interfaces.api.main import create_app


def test_migrated_case_survives_application_restart(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[2]
    database_url = f"sqlite:///{(tmp_path / 'lifecycle.db').as_posix()}"
    config = Config(str(root / "alembic.ini"))
    config.set_main_option("script_location", str(root / "migrations"))
    config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(config, "head")
    command.upgrade(config, "head")  # Repeated startup migration is harmless.

    first = build_case_application(database_url)
    try:
        with TestClient(create_app(first.service)) as client:
            assert client.get("/api/cases").json() == {"cases": []}
            response = client.post(
                "/api/cases", json={"name": "  合成持久化案件  ", "description": "合同争议"}
            )
            assert response.status_code == 201
            saved = response.json()
            assert str(UUID(saved["case_id"])) == saved["case_id"]
            assert saved["name"] == "合成持久化案件"
            for payload in (
                {"name": ""},
                {"name": "案" * 201},
                {"name": "案件", "description": "字" * 5001},
            ):
                assert client.post("/api/cases", json=payload).status_code == 422
    finally:
        first.engine.dispose()

    restarted = build_case_application(database_url)
    try:
        with TestClient(create_app(restarted.service)) as client:
            assert client.get(f"/api/cases/{saved['case_id']}").json() == saved
            assert client.get("/api/cases").json() == {"cases": [saved]}
            assert client.get("/api/cases/nonexistent").status_code == 404
    finally:
        restarted.engine.dispose()
