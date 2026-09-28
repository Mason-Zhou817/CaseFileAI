"""Local artifact creation and explicit copy-only restore confirmation."""

from __future__ import annotations

import json
import re
import shutil
from pathlib import Path
from threading import Lock
from typing import BinaryIO, Literal
from uuid import uuid4

from casefile_ai.application.data_contracts import (
    Artifact,
    BackupBackend,
    DataManagementError,
    ExportReader,
    RestorePreview,
    RestoreResult,
)
from casefile_ai.application.services.analysis_export import render_markdown

ID_PATTERN = re.compile(r"^[a-f\d]{8}-(?:[a-f\d]{4}-){3}[a-f\d]{12}$")
MAX_UPLOAD_BYTES = 10 * 1024**3


class DataManagementService:
    def __init__(
        self, root: Path, backup: BackupBackend, exports: ExportReader, schema: str
    ) -> None:
        self._root = root.resolve()
        self._backup = backup
        self._exports = exports
        self._schema = schema
        self._restore_lock = Lock()

    def _directory(self, group: str, identifier: str) -> Path:
        if not ID_PATTERN.fullmatch(identifier):
            raise DataManagementError("artifact_not_found")
        path = self._root / group / identifier
        if path.resolve() != path or path.is_symlink():
            raise DataManagementError("unsafe_path")
        return path

    def _artifact(self, kind: Literal["backup", "json", "markdown"]) -> tuple[str, Path, Path]:
        identifier = str(uuid4())
        directory = self._directory("artifacts", identifier)
        directory.mkdir(parents=True, exist_ok=False)
        filename = {
            "backup": f"casefile-ai-{identifier}.casefile-backup.zip",
            "json": "analysis.json",
            "markdown": "analysis.md",
        }[kind]
        return identifier, directory, directory / filename

    @staticmethod
    def _register(
        identifier: str, directory: Path, path: Path, kind: Literal["backup", "json", "markdown"]
    ) -> Artifact:
        artifact = Artifact(
            artifact_id=identifier, filename=path.name, size=path.stat().st_size, kind=kind
        )
        (directory / "artifact.json").write_text(artifact.model_dump_json(), encoding="utf-8")
        return artifact

    def create_backup(self) -> Artifact:
        identifier, directory, path = self._artifact("backup")
        self._backup.create(path)
        return self._register(identifier, directory, path, "backup")

    def export_analysis(self, case_id: str, format: Literal["json", "markdown"]) -> Artifact:
        report = self._exports.read(case_id)
        identifier, directory, path = self._artifact(format)
        text = report.model_dump_json(indent=2) if format == "json" else render_markdown(report)
        path.write_text(text, encoding="utf-8")
        return self._register(identifier, directory, path, format)

    def download(self, identifier: str) -> tuple[Path, Artifact]:
        directory = self._directory("artifacts", identifier)
        try:
            metadata = directory / "artifact.json"
            if metadata.is_symlink():
                raise DataManagementError("unsafe_path")
            artifact = Artifact.model_validate_json(metadata.read_bytes())
            expected = {
                "backup": f"casefile-ai-{identifier}.casefile-backup.zip",
                "json": "analysis.json",
                "markdown": "analysis.md",
            }[artifact.kind]
            path = directory / expected
            if (
                artifact.artifact_id != identifier
                or artifact.filename != expected
                or path.is_symlink()
                or path.resolve().parent != directory
                or not path.is_file()
            ):
                raise DataManagementError("artifact_not_found")
            return path, artifact
        except DataManagementError:
            raise
        except Exception:
            raise DataManagementError("artifact_not_found") from None

    def prepare_restore(self, stream: BinaryIO) -> RestorePreview:
        identifier = str(uuid4())
        directory = self._directory("restore-previews", identifier)
        directory.mkdir(parents=True, exist_ok=False)
        archive = directory / "upload.zip"
        try:
            size = 0
            with archive.open("xb") as target:
                while block := stream.read(1024 * 1024):
                    size += len(block)
                    if size > MAX_UPLOAD_BYTES:
                        raise DataManagementError("backup_too_large")
                    target.write(block)
            manifest, counts = self._backup.validate(archive, directory / "validated")
            preview = RestorePreview(
                restore_id=identifier,
                counts=counts,
                source_schema_version=manifest.database_schema_version,
                restored_schema_version=self._schema,
                total_size=manifest.total_size,
            )
            (directory / "preview.json").write_text(preview.model_dump_json(), encoding="utf-8")
            return preview
        except Exception:
            # Only the generated, task-owned preview directory is disposable.
            if directory.resolve() == self._directory("restore-previews", identifier):
                shutil.rmtree(directory)
            raise
        finally:
            archive.unlink(missing_ok=True)

    def confirm_restore(self, identifier: str, confirmation: str) -> RestoreResult:
        if confirmation != "create_new_copy":
            raise DataManagementError("restore_confirmation_required")
        directory = self._directory("restore-previews", identifier)
        destination = self._directory("restored", identifier)
        with self._restore_lock:
            if destination.exists():
                raise DataManagementError("restore_already_created")
            try:
                preview = RestorePreview.model_validate_json(
                    (directory / "preview.json").read_bytes()
                )
            except Exception:
                raise DataManagementError("restore_not_found") from None
            if preview.restore_id != identifier:
                raise DataManagementError("restore_not_found")
            counts = self._backup.restore(directory / "validated", destination)
            if counts != preview.counts:
                raise DataManagementError("database_references")
            result = RestoreResult(
                restore_id=identifier,
                data_directory=str(destination),
                database_path=str(destination / "casefile-ai.db"),
                counts=counts,
            )
            (destination / "restore-result.json").write_text(
                json.dumps(result.model_dump(), ensure_ascii=False, indent=2), encoding="utf-8"
            )
            return result
