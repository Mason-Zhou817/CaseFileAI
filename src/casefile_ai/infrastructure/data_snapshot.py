"""Consistent SQLite snapshots and structural source-chain checks, without model calls."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from contextlib import closing
from pathlib import Path
from typing import Any, cast

from sqlalchemy import Engine

from casefile_ai.application.data_contracts import DataManagementError
from casefile_ai.infrastructure.db.models import Base


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while block := source.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def snapshot_database(engine: Engine, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()

    def progress(_status: int, _remaining: int, _total: int) -> None:
        if time.monotonic() - started > 60:
            raise DataManagementError("backup_busy")

    connection = engine.raw_connection()
    try:
        source = cast(sqlite3.Connection, connection.driver_connection)
        with closing(sqlite3.connect(destination)) as target:
            source.backup(target, pages=128, progress=progress, sleep=0.05)
    finally:
        connection.close()


def open_database(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA trusted_schema=OFF")
    connection.execute("PRAGMA query_only=ON")
    connection.setlimit(sqlite3.SQLITE_LIMIT_LENGTH, 64 * 1024 * 1024)
    return connection


def database_revision(path: Path) -> str:
    with closing(open_database(path)) as connection:
        revisions = connection.execute("SELECT version_num FROM alembic_version").fetchall()
        if len(revisions) != 1:
            raise DataManagementError("schema_incompatible")
        return str(revisions[0][0])


def storage_path(root: Path, key: str) -> Path:
    # Imports generate UUID filenames; reject paths, ADS, links and junction escapes.
    if not key or any(c in key for c in "\\/:\x00") or key in {".", ".."}:
        raise DataManagementError("unsafe_path")
    path = root / key
    if path.is_symlink() or path.resolve().parent != root.resolve():
        raise DataManagementError("unsafe_path")
    if not key.endswith(".pdf") or not path.is_file():
        raise DataManagementError("missing_pdf")
    return path


def database_files(path: Path) -> list[tuple[str, str]]:
    with closing(open_database(path)) as connection:
        names = {
            str(row[0])
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        if "document_versions" not in names:
            return []
        return [
            (str(row[0]), str(row[1]))
            for row in connection.execute(
                "SELECT storage_key, content_sha256 FROM document_versions ORDER BY storage_key"
            )
        ]


def validate_database(path: Path, *, current_schema: bool = True) -> dict[str, int]:
    """Validate FKs plus JSON links that are not represented by SQL foreign keys."""
    with closing(open_database(path)) as connection:
        if [row[0] for row in connection.execute("PRAGMA integrity_check")] != ["ok"]:
            raise DataManagementError("database_integrity")
        if connection.execute("PRAGMA foreign_key_check").fetchone() is not None:
            raise DataManagementError("database_references")
        if connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type IN ('trigger', 'view') LIMIT 1"
        ).fetchone():
            raise DataManagementError("schema_incompatible")
        virtual = connection.execute(
            "SELECT name FROM sqlite_master WHERE sql LIKE 'CREATE VIRTUAL TABLE%'"
        ).fetchall()
        if any(row[0] != "chunk_fts" for row in virtual):
            raise DataManagementError("schema_incompatible")
        tables = {
            str(row[0])
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        if not current_schema:
            return {}
        if not (set(Base.metadata.tables) | {"chunk_fts"}).issubset(tables):
            raise DataManagementError("schema_incompatible")
        counts = {
            name: int(connection.execute(f'SELECT COUNT(*) FROM "{name}"').fetchone()[0])
            for name in Base.metadata.tables
        }
        versions = {
            row["document_version_id"]: row
            for row in connection.execute(
                "SELECT v.*, d.case_id FROM document_versions v JOIN documents d USING(document_id)"
            )
        }
        pages = {row["page_id"]: row for row in connection.execute("SELECT * FROM pages")}
        for version_id, version in versions.items():
            numbers = sorted(
                p["page_number"] for p in pages.values() if p["document_version_id"] == version_id
            )
            if numbers != list(range(1, version["page_count"] + 1)):
                raise DataManagementError("database_references")
        for page in pages.values():
            if hashlib.sha256(page["text"].encode()).hexdigest() != page["text_hash"]:
                raise DataManagementError("database_references")
        chunks = {row["chunk_id"]: row for row in connection.execute("SELECT * FROM chunks")}
        for chunk in chunks.values():
            page = pages[chunk["page_id"]]
            version = versions[page["document_version_id"]]
            if (
                chunk["case_id"] != version["case_id"]
                or page["text"][chunk["start_offset"] : chunk["end_offset"]] != chunk["text"]
                or hashlib.sha256(chunk["text"].encode()).hexdigest() != chunk["text_hash"]
            ):
                raise DataManagementError("database_references")
        evidence = {row["evidence_id"]: row for row in connection.execute("SELECT * FROM evidence")}
        for item in evidence.values():
            chunk = chunks[item["chunk_id"]]
            if (
                item["case_id"] != chunk["case_id"]
                or item["quote"] != chunk["text"]
                or item["start_offset"] != chunk["start_offset"]
                or item["end_offset"] != chunk["end_offset"]
            ):
                raise DataManagementError("database_references")
        answers = {
            row["answer_id"]: row
            for row in connection.execute(
                "SELECT a.*, q.case_id FROM answers a JOIN questions q USING(question_id)"
            )
        }
        source_claims: dict[str, set[str]] = {}
        declared_citations: dict[tuple[str, str], set[str]] = {}
        for answer_id, answer in answers.items():
            source_claims[answer_id] = {x["claim_id"] for x in json.loads(answer["claims_json"])}
            for source_claim in json.loads(answer["claims_json"]):
                declared_citations[(answer_id, source_claim["claim_id"])] = set(
                    source_claim["citation_ids"]
                )
            for evidence_id in json.loads(answer["evidence_ids_json"]):
                if (
                    evidence_id not in evidence
                    or evidence[evidence_id]["case_id"] != answer["case_id"]
                ):
                    raise DataManagementError("database_references")
        citations = {
            row["answer_citation_id"]: row
            for row in connection.execute("SELECT * FROM answer_citations")
        }
        for citation in citations.values():
            answer = answers[citation["answer_id"]]
            if (
                citation["claim_id"] not in source_claims[answer["answer_id"]]
                or evidence[citation["evidence_id"]]["case_id"] != answer["case_id"]
            ):
                raise DataManagementError("database_references")
        for (answer_id, source_claim_id), expected in declared_citations.items():
            actual = {
                c["evidence_id"]
                for c in citations.values()
                if c["answer_id"] == answer_id and c["claim_id"] == source_claim_id
            }
            if actual != expected:
                raise DataManagementError("database_references")
        claims = {row["claim_id"]: row for row in connection.execute("SELECT * FROM claims")}
        for claim in claims.values():
            if claim["source_answer_claim_id"] not in source_claims[claim["answer_id"]]:
                raise DataManagementError("database_references")
        linked: set[str] = set()
        claim_evidence: dict[str, set[str]] = {}
        for link in connection.execute("SELECT * FROM claim_evidence"):
            claim, citation = claims[link["claim_id"]], citations[link["answer_citation_id"]]
            if (
                claim["answer_id"] != citation["answer_id"]
                or claim["source_answer_claim_id"] != citation["claim_id"]
            ):
                raise DataManagementError("database_references")
            linked.add(claim["claim_id"])
            claim_evidence.setdefault(claim["claim_id"], set()).add(citation["evidence_id"])
        if linked != set(claims):
            raise DataManagementError("database_references")
        for result in connection.execute("SELECT * FROM contradiction_results"):
            for side in ("left", "right"):
                claim = claims[result[f"{side}_claim_id"]]
                if answers[claim["answer_id"]]["case_id"] != result["case_id"]:
                    raise DataManagementError("database_references")
                for cite in cast(list[dict[str, Any]], json.loads(result[f"{side}_evidence_json"])):
                    item = evidence.get(cite["evidence_id"])
                    if item is None:
                        raise DataManagementError("database_references")
                    if (
                        cite["evidence_id"] not in claim_evidence[claim["claim_id"]]
                        or cite.get("claim_id") != claim["source_answer_claim_id"]
                    ):
                        raise DataManagementError("database_references")
                    chunk = chunks[item["chunk_id"]]
                    page = pages[chunk["page_id"]]
                    version = versions[page["document_version_id"]]
                    if any(
                        cite.get(field) != value
                        for field, value in (
                            ("document_id", version["document_id"]),
                            ("document_version_id", version["document_version_id"]),
                            ("page_id", page["page_id"]),
                            ("page_number", page["page_number"]),
                            ("filename", version["filename"]),
                            ("quote", item["quote"]),
                        )
                    ):
                        raise DataManagementError("database_references")
        if connection.execute(
            "SELECT 1 FROM chunk_fts f LEFT JOIN chunks c ON c.chunk_id=f.chunk_id "
            "WHERE c.chunk_id IS NULL OR c.case_id != f.case_id LIMIT 1"
        ).fetchone():
            raise DataManagementError("database_references")
        return counts
