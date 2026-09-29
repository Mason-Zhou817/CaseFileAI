"""Synthetic lifecycle, complete snapshots, copy-only recovery and provenance exports."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import stat
import zipfile
from concurrent.futures import ThreadPoolExecutor
from io import BytesIO
from pathlib import Path
from threading import Event
from typing import Any
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient
from test_material_status import configured as configured
from test_workspace_e2e import EndToEndLLM, _pdf

from casefile_ai.application.data_contracts import AnalysisExport
from casefile_ai.application.services.claim_coverage import DefaultClaimCoverageService
from casefile_ai.application.services.claims import DefaultClaimExtractionService
from casefile_ai.application.services.conflict_candidates import DefaultConflictCandidateService
from casefile_ai.application.services.contradictions import DefaultContradictionService
from casefile_ai.application.services.qa import DefaultQAService
from casefile_ai.application.services.timeline import DefaultTimelineService
from casefile_ai.application.services.workspace import CaseWorkspaceService
from casefile_ai.config import Settings
from casefile_ai.infrastructure.data_snapshot import sha256, validate_database
from casefile_ai.infrastructure.db.claims import SqlAlchemyClaimRepository
from casefile_ai.infrastructure.db.contradictions import SqlAlchemyContradictionRepository
from casefile_ai.infrastructure.db.migration import migration_config
from casefile_ai.infrastructure.db.models import CaseModel
from casefile_ai.infrastructure.db.qa import SqlAlchemyQARepository
from casefile_ai.infrastructure.db.session import build_session_factory, session_scope
from casefile_ai.interfaces.api.main import create_app


@pytest.fixture
def populated(configured: Settings):
    # Exercise a SQLite path outside data_dir: backing up data_dir alone must not work.
    configured.database_url = (
        f"sqlite:///{(configured.app_home / 'external' / 'live.db').as_posix()}"
    )
    with TestClient(create_app(settings=configured)) as client:
        runtime = client.app.state.runtime
        factory = build_session_factory(runtime.cases.engine)
        answers = SqlAlchemyQARepository(factory)
        claims = SqlAlchemyClaimRepository(factory)
        results = SqlAlchemyContradictionRepository(factory)
        llm = EndToEndLLM()
        candidates = DefaultConflictCandidateService(answers, claims)
        workspace = CaseWorkspaceService(
            runtime.cases.service,
            answers,
            claims,
            results,
            DefaultClaimExtractionService(answers, claims, llm),
            DefaultClaimCoverageService(answers, claims),
            candidates,
            DefaultContradictionService(candidates, results, llm),
            DefaultTimelineService(answers, claims, results),
            llm.model_version,
        )
        case = client.post("/api/cases", json={"name": "合成数据生命周期"}).json()["case_id"]
        versions = []
        for number, amount in enumerate(("450000", "500000")):
            data = {"document_id": versions[0]["document_id"]} if versions else {}
            document = client.post(
                f"/api/cases/{case}/documents",
                data=data,
                files={
                    "file": (f"合成金额-v{number + 1}.pdf", _pdf(f"交易金额为{amount}元。")),
                },
            )
            assert document.status_code == 200
            versions.append(document.json())
            runtime.retrieval.index_case(case)
            answer = DefaultQAService(answers, runtime.retrieval, llm).ask_case(
                case, "第一笔交易金额？" if number == 0 else "第二笔交易金额？"
            )
            assert not answer.abstained
            workspace.extract(case, answer.answer_id)
        detected = workspace.detect(case)
        assert len(detected) == 1
        # A persisted uncertain fixture must remain uncertain in both export formats.
        with runtime.cases.engine.begin() as connection:
            connection.exec_driver_sql(
                "UPDATE contradiction_results SET verdict='uncertain', "
                "basis='amount_scope_unaligned', reason='合成待确认结果'"
            )
        yield client, case, versions, configured


def backup(client: TestClient) -> bytes:
    response = client.post("/api/data/backups")
    assert response.status_code == 200, response.text
    download = client.get(response.json()["download_url"])
    assert download.status_code == 200
    assert "attachment" in download.headers["content-disposition"]
    return download.content


def preview(client: TestClient, data: bytes):
    return client.post("/api/data/restores/preview", files={"file": ("backup.zip", data)})


def test_feedback_backup_restore_and_cross_case_validation(
    populated, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    client, case, _, _ = populated
    answer_id = client.get(f"/api/cases/{case}/answers").json()[0]["answer_id"]
    note = "合成反馈正文不进入日志"
    response = client.post(
        f"/api/cases/{case}/feedback",
        json={
            "answer_id": answer_id,
            "rating": "incorrect",
            "category": "citation",
            "note": note,
        },
    )
    assert response.status_code == 200, response.text
    assert response.json()["review_status"] == "pending"
    assert note not in caplog.text
    assert client.get(f"/api/cases/{case}/feedback/summary").json()["total"] == 1
    exported = client.post(f"/api/cases/{case}/exports/json").json()
    report = client.get(exported["download_url"]).json()
    assert report["feedback_summary"]["incorrect_by_category"]["citation"] == 1
    assert note not in json.dumps(report, ensure_ascii=False)
    client.patch(f"/api/cases/{case}/archive", json={"archived": True})
    assert client.get(f"/api/cases/{case}/feedback/summary").json()["total"] == 1
    data = backup(client)
    with zipfile.ZipFile(BytesIO(data)) as archive:
        database = tmp_path / "feedback.db"
        database.write_bytes(archive.read("database/casefile-ai.db"))
    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT note FROM feedback").fetchone()[0] == note
    restore = preview(client, data)
    assert restore.status_code == 200, restore.text
    restored = client.post(
        f"/api/data/restores/{restore.json()['restore_id']}/confirm",
        json={"confirmation": "create_new_copy"},
    )
    assert restored.status_code == 200, restored.text
    with sqlite3.connect(restored.json()["database_path"]) as connection:
        assert connection.execute("SELECT COUNT(*) FROM feedback").fetchone()[0] == 1

    other = client.post("/api/cases", json={"name": "合成其他案件"}).json()["case_id"]
    with sqlite3.connect(database) as connection:
        connection.execute(
            "INSERT INTO cases(case_id,name,status,created_at,updated_at) "
            "SELECT ?,name,status,created_at,updated_at FROM cases LIMIT 1",
            (other,),
        )
        connection.execute("UPDATE feedback SET case_id=?", (other,))

    def corrupt(files, manifest):
        files["database/casefile-ai.db"] = database.read_bytes()
        prior = manifest["database"]["size"]
        manifest["database"]["sha256"] = sha256(database)
        manifest["database"]["size"] = database.stat().st_size
        manifest["total_size"] += database.stat().st_size - prior

    assert preview(client, rewrite(data, corrupt)).status_code == 422


def rewrite(data: bytes, change: Any) -> bytes:
    with zipfile.ZipFile(BytesIO(data)) as archive:
        files = {name: archive.read(name) for name in archive.namelist()}
    manifest = json.loads(files["manifest.json"])
    change(files, manifest)
    files["manifest.json"] = json.dumps(manifest).encode()
    stream = BytesIO()
    with zipfile.ZipFile(stream, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, content in files.items():
            archive.writestr(name, content)
    return stream.getvalue()


def test_rename_archive_roundtrip_preserves_ids_and_history(populated) -> None:
    client, case, versions, _ = populated
    before = client.get(f"/api/cases/{case}/answers").json()
    response = client.patch(f"/api/cases/{case}/name", json={"name": "  合成新名称  "})
    assert response.status_code == 200 and response.json()["name"] == "合成新名称"
    assert response.json()["case_id"] == case
    for name in ("", "   ", "x" * 201):
        assert client.patch(f"/api/cases/{case}/name", json={"name": name}).status_code == 422
    assert (
        client.patch(f"/api/cases/{case}/archive", json={"archived": True}).json()["status"]
        == "archived"
    )
    assert client.get("/api/cases").json()["cases"] == []
    assert client.get("/api/cases?include_archived=true").json()["cases"][0]["case_id"] == case
    assert client.get(f"/api/cases/{case}/answers").json() == before
    for version in versions:
        assert (
            client.get(
                f"/api/documents/{version['document_id']}/original",
                params={
                    "document_version_id": version["document_version_id"],
                },
            ).status_code
            == 200
        )
    assert client.post(f"/api/cases/{case}/exports/json").status_code == 200
    assert (
        client.patch(f"/api/cases/{case}/archive", json={"archived": False}).json()["status"]
        == "active"
    )
    assert client.get("/api/cases").json()["cases"][0]["case_id"] == case
    assert client.delete(f"/api/cases/{case}").status_code == 405


def test_complete_backup_manifest_history_and_no_secrets(populated) -> None:
    client, _, versions, settings = populated
    secret = "synthetic-secret-do-not-backup-4298"
    settings.llm_api_key = secret
    (settings.data_dir / ".env").write_text(secret, encoding="utf-8")
    (settings.data_dir / "debug.log").write_text(secret, encoding="utf-8")
    (settings.data_dir / "orphan.pdf").write_text(secret, encoding="utf-8")
    data = backup(client)
    with zipfile.ZipFile(BytesIO(data)) as archive:
        manifest = json.loads(archive.read("manifest.json"))
        assert manifest["database_schema_version"] == "20260929_0008"
        assert manifest["total_file_count"] == 3
        assert len(manifest["files"]) == 2
        assert {Path(x["path"]).name for x in manifest["files"]} == {
            v["document_version_id"] + ".pdf" for v in versions
        }
        assert set(archive.namelist()) == {
            "manifest.json",
            "database/casefile-ai.db",
            *[x["path"] for x in manifest["files"]],
        }
        for item in [manifest["database"], *manifest["files"]]:
            content = archive.read(item["path"])
            assert hashlib.sha256(content).hexdigest() == item["sha256"]
            assert len(content) == item["size"] and secret.encode() not in content
        assert secret not in archive.read("manifest.json").decode()
        assert manifest["total_size"] == sum(
            x["size"] for x in [manifest["database"], *manifest["files"]]
        )


def test_restore_verifies_copy_and_never_changes_current_data(populated) -> None:
    client, case, versions, settings = populated
    original_hash = sha256(settings.database_path)
    response = preview(client, backup(client))
    assert response.status_code == 200, response.text
    identifier = response.json()["restore_id"]
    assert response.json()["counts"]["cases"] == 1
    assert response.json()["counts"]["document_versions"] == 2
    assert response.json()["counts"]["answers"] == response.json()["counts"]["claims"] == 2
    assert client.post(f"/api/data/restores/{identifier}/confirm", json={}).status_code == 422
    assert (
        client.post(
            f"/api/data/restores/{identifier}/confirm", json={"confirmation": "overwrite"}
        ).status_code
        == 422
    )
    restored = client.post(
        f"/api/data/restores/{identifier}/confirm", json={"confirmation": "create_new_copy"}
    )
    assert restored.status_code == 200, restored.text
    result = restored.json()
    target = Path(result["data_directory"])
    assert target != settings.data_dir and not target.is_relative_to(settings.data_dir)
    assert Path(result["database_path"]) != settings.database_path
    assert sha256(settings.database_path) == original_hash
    assert validate_database(Path(result["database_path"])) == result["counts"]
    with sqlite3.connect(result["database_path"]) as database:
        assert database.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert not database.execute("PRAGMA foreign_key_check").fetchall()
        assert database.execute("SELECT case_id FROM cases").fetchone()[0] == case
    for version in versions:
        assert (
            sha256(target / "originals" / (version["document_version_id"] + ".pdf"))
            == version["content_sha256"]
        )
    assert (
        client.post(
            f"/api/data/restores/{identifier}/confirm", json={"confirmation": "create_new_copy"}
        ).status_code
        == 409
    )
    # A restored runtime reads its history and local index without changing IDs.
    copy_settings = settings.model_copy(
        update={
            "data_dir": target,
            "database_url": f"sqlite:///{Path(result['database_path']).as_posix()}",
            "log_dir": target / "test-logs",
        }
    )
    with TestClient(create_app(settings=copy_settings)) as restored_client:
        assert len(restored_client.get(f"/api/cases/{case}/answers").json()) == 2
        assert restored_client.get(f"/api/cases/{case}/materials").json()["status"] == "ready"
        for version in versions:
            assert (
                restored_client.get(
                    f"/api/documents/{version['document_id']}/original",
                    params={"document_version_id": version["document_version_id"]},
                ).status_code
                == 200
            )


@pytest.mark.parametrize(
    "damage",
    [
        "missing_pdf",
        "checksum",
        "database",
        "future_format",
        "future_schema",
        "future_app",
        "extra_file",
        "manifest",
    ],
)
def test_invalid_backups_are_rejected_without_modifying_live_data(populated, damage: str) -> None:
    client, _, _, settings = populated
    data = backup(client)
    original_hash = sha256(settings.database_path)

    def change(files, manifest):
        if damage == "missing_pdf":
            files.pop(manifest["files"][0]["path"])
        elif damage == "checksum":
            files[manifest["files"][0]["path"]] = b"corrupt"
        elif damage == "database":
            files[manifest["database"]["path"]] = b"not a database"
            manifest["database"]["sha256"] = hashlib.sha256(b"not a database").hexdigest()
            manifest["total_size"] += len(b"not a database") - manifest["database"]["size"]
            manifest["database"]["size"] = len(b"not a database")
        elif damage == "future_format":
            manifest["backup_format_version"] = 999
        elif damage == "future_schema":
            manifest["database_schema_version"] = "99999999_9999"
        elif damage == "future_app":
            manifest["application_version"] = "999.0.0"
        elif damage == "extra_file":
            files["secret.env"] = b"must not be extracted"
        else:
            manifest["database"]["path"] = "../outside.db"

    response = preview(client, rewrite(data, change))
    assert response.status_code == 422
    assert sha256(settings.database_path) == original_hash
    assert not (settings.app_home / "data-management/restored").exists()


@pytest.mark.parametrize(
    "name",
    ["../escape.pdf", "/absolute.pdf", "C:/escape.pdf", r"..\escape.pdf", "files//escape.pdf"],
)
def test_zip_traversal_entries_are_rejected(populated, name: str) -> None:
    client, _, _, settings = populated
    response = preview(
        client, rewrite(backup(client), lambda files, _: files.update({name: b"unsafe"}))
    )
    assert response.status_code == 422
    assert response.json()["error"]["category"] == "unsafe_archive"
    assert not (settings.app_home / "escape.pdf").exists()


def test_zip_symlink_and_duplicate_entries_are_rejected(populated) -> None:
    client, _, _, _ = populated
    data = backup(client)
    for duplicate in (False, True):
        buffer = BytesIO(data)
        with zipfile.ZipFile(buffer, "a") as archive:
            if duplicate:
                with pytest.warns(UserWarning):
                    archive.writestr("manifest.json", "{}")
            else:
                entry = zipfile.ZipInfo("files/originals/symlink.pdf")
                entry.create_system = 3
                entry.external_attr = (stat.S_IFLNK | 0o777) << 16
                archive.writestr(entry, "/outside")
        assert preview(client, buffer.getvalue()).json()["error"]["category"] == "unsafe_archive"


@pytest.mark.parametrize("missing", [False, True])
def test_backup_fails_instead_of_skipping_bad_originals(populated, missing: bool) -> None:
    client, _, versions, settings = populated
    path = settings.data_dir / "originals" / (versions[0]["document_version_id"] + ".pdf")
    if missing:
        path.unlink()
    else:
        path.write_bytes(b"tampered original")
    response = client.post("/api/data/backups")
    assert response.status_code == 422
    assert response.json()["error"]["category"] == ("missing_pdf" if missing else "pdf_checksum")
    assert not list((settings.app_home / "data-management/artifacts").rglob("artifact.json"))


def test_backup_consistency_during_import_and_index_writes(populated) -> None:
    client, case, _, settings = populated
    runtime = client.app.state.runtime

    def importing():
        for amount in ("600000", "700000", "800000"):
            runtime.ingestion.upload_document(
                case, BytesIO(_pdf(f"交易金额为{amount}元。")), amount + ".pdf"
            )
            runtime.retrieval.index_case(case)

    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(importing)
        data = backup(client)
        future.result(timeout=30)
    response = preview(client, data)
    assert response.status_code == 200, response.text
    assert 2 <= response.json()["counts"]["document_versions"] <= 5
    assert settings.database_path.is_file()


def test_exports_preserve_old_citations_uncertainty_and_never_call_models(
    populated, monkeypatch
) -> None:
    client, case, _, _ = populated
    runtime = client.app.state.runtime
    guard = Mock(side_effect=AssertionError("export must not invoke models or embeddings"))
    monkeypatch.setattr(runtime.llm, "generate_structured", guard)
    monkeypatch.setattr(runtime.embeddings, "embed_documents", guard)
    monkeypatch.setattr(runtime.embeddings, "embed_query", guard)
    client.patch(f"/api/cases/{case}/archive", json={"archived": True})
    for format in ("json", "markdown"):
        response = client.post(f"/api/cases/{case}/exports/{format}")
        assert response.status_code == 200, response.text
        download = client.get(response.json()["download_url"])
        assert download.status_code == 200
        if format == "json":
            report = AnalysisExport.model_validate_json(download.content)
            assert report.case.case_id == case and report.case.status == "archived"
            assert len(report.documents[0].versions) == 2
            assert len(report.answers) == len(report.claims) == len(report.timeline) == 2
            assert report.coverage and report.candidates
            assert report.contradictions[0]["verdict"] == "uncertain"
            old = next(a for a in report.answers if "第一" in a["question"])
            assert old["citations"][0]["version_number"] == 1
            assert not old["citations"][0]["is_current_version"]
            assert report.claims[0]["sources"]
        else:
            assert "来源：v1（非当前版本）" in download.text
            assert "第 1 页" in download.text and "合成金额-v1.pdf" in download.text
            assert "待确认 / uncertain" in download.text
            assert "### 已判定矛盾" not in download.text
            assert "不代表系统已穷尽卷宗所有事实" in download.text
            assert "请妥善保存" in download.text
    guard.assert_not_called()


@pytest.mark.parametrize(
    "identifier", ["not-a-uuid", "../../.env", "00000000-0000-0000-0000-000000000000"]
)
def test_download_only_accepts_registered_generated_ids(populated, identifier: str) -> None:
    client, _, _, _ = populated
    assert client.get(f"/api/data/artifacts/{identifier}").status_code in {404, 422}
    assert client.get("/download", params={"path": r"C:\Windows\win.ini"}).status_code == 404


def test_restore_confirmation_revalidates_staging(populated) -> None:
    client, _, _, settings = populated
    data = preview(client, backup(client)).json()
    directory = (
        settings.app_home / "data-management/restore-previews" / data["restore_id"] / "validated"
    )
    pdf = next((directory / "files/originals").glob("*.pdf"))
    pdf.write_bytes(b"tampered after preview")
    original_hash = sha256(settings.database_path)
    response = client.post(
        f"/api/data/restores/{data['restore_id']}/confirm", json={"confirmation": "create_new_copy"}
    )
    assert response.status_code == 422
    assert sha256(settings.database_path) == original_hash


def test_known_older_schema_is_migrated_only_in_new_copy(populated, tmp_path: Path) -> None:
    from alembic import command

    client, _, _, settings = populated
    database = tmp_path / "old.db"
    data = backup(client)
    with zipfile.ZipFile(BytesIO(data)) as archive:
        database.write_bytes(archive.read("database/casefile-ai.db"))
    old_settings = settings.model_copy(update={"database_url": f"sqlite:///{database.as_posix()}"})
    command.downgrade(migration_config(old_settings), "20260923_0006")

    def change(files, manifest):
        files["database/casefile-ai.db"] = database.read_bytes()
        prior = manifest["database"]["size"]
        manifest["database"]["sha256"] = sha256(database)
        manifest["database"]["size"] = database.stat().st_size
        manifest["total_size"] += database.stat().st_size - prior
        manifest["database_schema_version"] = "20260923_0006"

    response = preview(client, rewrite(data, change))
    assert response.status_code == 200, response.text
    assert response.json()["source_schema_version"] == "20260923_0006"
    restored = client.post(
        f"/api/data/restores/{response.json()['restore_id']}/confirm",
        json={"confirmation": "create_new_copy"},
    )
    assert restored.status_code == 200
    with sqlite3.connect(restored.json()["database_path"]) as connection:
        assert (
            connection.execute("SELECT version_num FROM alembic_version").fetchone()[0]
            == "20260929_0008"
        )
    with sqlite3.connect(database) as connection:
        assert (
            connection.execute("SELECT version_num FROM alembic_version").fetchone()[0]
            == "20260923_0006"
        )


def test_online_backup_excludes_uncommitted_wal_writes(populated) -> None:
    client, _, _, _ = populated
    engine = client.app.state.runtime.cases.engine
    with engine.connect() as connection:
        connection.exec_driver_sql("PRAGMA journal_mode=WAL")
    entered, release = Event(), Event()

    def writer() -> None:
        with session_scope(build_session_factory(engine)) as session:
            session.add(
                CaseModel(case_id="00000000-0000-0000-0000-000000000099", name="合成未提交案件")
            )
            session.flush()
            entered.set()
            assert release.wait(15)

    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(writer)
        try:
            assert entered.wait(10)
            copied = backup(client)
        finally:
            release.set()
        future.result(timeout=10)
    assert len(client.get("/api/cases").json()["cases"]) == 2
    result = preview(client, copied)
    assert result.status_code == 200, result.text
    assert result.json()["counts"]["cases"] == 1


@pytest.mark.parametrize("broken", ["fk", "json", "contradiction_source", "page_hash", "fts"])
def test_restore_rejects_broken_source_chains_even_with_valid_file_checksums(
    populated,
    tmp_path: Path,
    broken: str,
) -> None:
    client, _, _, settings = populated
    data = backup(client)
    original_hash = sha256(settings.database_path)
    database = tmp_path / "broken.db"
    with zipfile.ZipFile(BytesIO(data)) as archive:
        database.write_bytes(archive.read("database/casefile-ai.db"))
    with sqlite3.connect(database) as connection:
        if broken == "fk":
            connection.execute("UPDATE answer_citations SET evidence_id='missing'")
        elif broken == "json":
            connection.execute("UPDATE claims SET source_answer_claim_id='missing'")
        elif broken == "contradiction_source":
            value = json.loads(
                connection.execute(
                    "SELECT left_evidence_json FROM contradiction_results"
                ).fetchone()[0]
            )
            value[0]["document_version_id"] = "wrong-version"
            connection.execute(
                "UPDATE contradiction_results SET left_evidence_json=?", (json.dumps(value),)
            )
        elif broken == "page_hash":
            connection.execute("UPDATE pages SET text_hash=?", ("0" * 64,))
        else:
            connection.execute("UPDATE chunk_fts SET case_id='wrong-case'")

    def change(files, manifest):
        prior = manifest["database"]["size"]
        files["database/casefile-ai.db"] = database.read_bytes()
        manifest["database"]["sha256"] = sha256(database)
        manifest["database"]["size"] = database.stat().st_size
        manifest["total_size"] += database.stat().st_size - prior

    response = preview(client, rewrite(data, change))
    assert response.status_code == 422
    assert response.json()["error"]["category"] == "database_references"
    assert sha256(settings.database_path) == original_hash
