"""Verified full backups and copy-only restore; never overwrite live storage."""

from __future__ import annotations

import re
import shutil
import stat
import tempfile
import zipfile
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath

from alembic.script import ScriptDirectory
from pydantic import ValidationError
from sqlalchemy import Engine

from casefile_ai import __version__
from casefile_ai.application.data_contracts import BackupFile, BackupManifest, DataManagementError
from casefile_ai.config import Settings
from casefile_ai.infrastructure.data_snapshot import (
    database_files,
    database_revision,
    sha256,
    snapshot_database,
    storage_path,
    validate_database,
)
from casefile_ai.infrastructure.db.migration import (
    expected_revision,
    migrate_database,
    migration_config,
)

FORMAT_VERSION = 1
DATABASE_ENTRY = "database/casefile-ai.db"
MAX_ARCHIVE_BYTES = 10 * 1024**3
MAX_EXPANDED_BYTES = 20 * 1024**3
MAX_ENTRIES = 50_000


def safe_entry(name: str) -> str:
    path = PurePosixPath(name)
    if (
        not name
        or "\\" in name
        or ":" in name
        or "\x00" in name
        or path.is_absolute()
        or any(part in {".", "..", ""} for part in name.split("/"))
        or str(path) != name
    ):
        raise DataManagementError("unsafe_archive")
    return name


class SqliteBackupStorage:
    def __init__(self, settings: Settings, engine: Engine) -> None:
        self._settings = settings
        self._engine = engine
        self._head = expected_revision(settings)
        self._revisions = {
            r.revision
            for r in ScriptDirectory.from_config(migration_config(settings)).walk_revisions()
        }

    def _compatible(self, manifest: BackupManifest) -> None:
        if manifest.backup_format_version != FORMAT_VERSION:
            raise DataManagementError("backup_format_incompatible")
        pattern = r"^(\d+)\.(\d+)\.(\d+)$"
        remote, local = (
            re.fullmatch(pattern, manifest.application_version),
            re.fullmatch(pattern, __version__),
        )
        if not remote or not local:
            raise DataManagementError("application_version_incompatible")
        if tuple(map(int, remote.groups())) > tuple(map(int, local.groups())):
            raise DataManagementError("future_backup")
        if manifest.database_schema_version not in self._revisions:
            raise DataManagementError("future_schema")
        try:
            created = datetime.fromisoformat(manifest.created_at)
            if created.tzinfo is None:
                raise ValueError("timezone_missing")
        except ValueError:
            raise DataManagementError("invalid_manifest") from None

    def create(self, destination: Path) -> BackupManifest:
        try:
            with tempfile.TemporaryDirectory(dir=destination.parent) as temp:
                root = Path(temp)
                database = root / DATABASE_ENTRY
                snapshot_database(self._engine, database)
                validate_database(database)
                revision = database_revision(database)
                if revision != self._head:
                    raise DataManagementError("schema_incompatible")
                files: list[BackupFile] = []
                for key, expected_hash in database_files(database):
                    source = storage_path(self._settings.data_dir / "originals", key)
                    relative = "files/originals/" + key
                    target = root / relative
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(source, target)
                    if sha256(target) != expected_hash:
                        raise DataManagementError("pdf_checksum")
                    files.append(
                        BackupFile(path=relative, sha256=expected_hash, size=target.stat().st_size)
                    )
                db_file = BackupFile(
                    path=DATABASE_ENTRY, sha256=sha256(database), size=database.stat().st_size
                )
                manifest = BackupManifest(
                    backup_format_version=FORMAT_VERSION,
                    application_version=__version__,
                    created_at=datetime.now(UTC).isoformat(),
                    database_schema_version=revision,
                    database=db_file,
                    files=files,
                    total_file_count=1 + len(files),
                    total_size=db_file.size + sum(item.size for item in files),
                )
                with zipfile.ZipFile(destination, "x", compression=zipfile.ZIP_DEFLATED) as archive:
                    archive.writestr("manifest.json", manifest.model_dump_json(indent=2))
                    for item in [db_file, *files]:
                        archive.write(root / item.path, item.path)
                # Verify the finished product, including all archive contents and source links.
                self.validate(destination, root / "verified")
                return manifest
        except DataManagementError:
            destination.unlink(missing_ok=True)
            raise
        except Exception:
            destination.unlink(missing_ok=True)
            raise DataManagementError("backup_failed") from None

    def validate(self, archive_path: Path, staging: Path) -> tuple[BackupManifest, dict[str, int]]:
        try:
            if archive_path.stat().st_size > MAX_ARCHIVE_BYTES:
                raise DataManagementError("backup_too_large")
            with zipfile.ZipFile(archive_path) as archive:
                entries = archive.infolist()
                if len(entries) > MAX_ENTRIES:
                    raise DataManagementError("backup_too_large")
                names: set[str] = set()
                total = 0
                for entry in entries:
                    name = safe_entry(entry.filename)
                    mode = entry.external_attr >> 16
                    if (
                        name in names
                        or entry.is_dir()
                        or entry.flag_bits & 1
                        or stat.S_ISLNK(mode)
                        or stat.S_IFMT(mode) not in {0, stat.S_IFREG}
                        or entry.file_size > MAX_EXPANDED_BYTES
                        or entry.file_size / max(entry.compress_size, 1) > 2000
                    ):
                        raise DataManagementError("unsafe_archive")
                    total += entry.file_size
                    names.add(name)
                if total > MAX_EXPANDED_BYTES:
                    raise DataManagementError("backup_too_large")
                if (
                    "manifest.json" not in names
                    or archive.getinfo("manifest.json").file_size > 8 * 1024**2
                ):
                    raise DataManagementError("invalid_manifest")
                manifest = BackupManifest.model_validate_json(archive.read("manifest.json"))
                self._compatible(manifest)
                payload = [manifest.database, *manifest.files]
                expected_names = {item.path for item in payload}
                if (
                    manifest.database.path != DATABASE_ENTRY
                    or len(expected_names) != len(payload)
                    or names != expected_names | {"manifest.json"}
                    or manifest.total_file_count != len(payload)
                    or manifest.total_size != sum(item.size for item in payload)
                ):
                    raise DataManagementError("invalid_manifest")
                for item in manifest.files:
                    if not re.fullmatch(r"files/originals/[a-f\d-]{36}\.pdf", item.path):
                        raise DataManagementError("unsafe_archive")
                staging.mkdir(parents=True, exist_ok=False)
                for item in payload:
                    safe_entry(item.path)
                    destination = staging / item.path
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    if destination.resolve().is_relative_to(staging.resolve()) is False:
                        raise DataManagementError("unsafe_archive")
                    digest_size = 0
                    with archive.open(item.path) as source, destination.open("xb") as target:
                        while block := source.read(1024 * 1024):
                            digest_size += len(block)
                            if digest_size > item.size:
                                raise DataManagementError("checksum_mismatch")
                            target.write(block)
                    if digest_size != item.size or sha256(destination) != item.sha256:
                        raise DataManagementError("checksum_mismatch")
                (staging / "manifest.json").write_text(
                    manifest.model_dump_json(indent=2), encoding="utf-8"
                )
            counts = self._validate_staged(staging, manifest)
            return manifest, counts
        except DataManagementError:
            raise
        except ValidationError:
            raise DataManagementError("invalid_manifest") from None
        except Exception:
            raise DataManagementError("invalid_backup") from None

    def _validate_staged(self, staging: Path, manifest: BackupManifest) -> dict[str, int]:
        self._compatible(manifest)
        for item in [manifest.database, *manifest.files]:
            safe_entry(item.path)
            path = staging / item.path
            if (
                path.is_symlink()
                or not path.resolve().is_relative_to(staging.resolve())
                or not path.is_file()
                or path.stat().st_size != item.size
                or sha256(path) != item.sha256
            ):
                raise DataManagementError("checksum_mismatch")
        database = staging / DATABASE_ENTRY
        if database_revision(database) != manifest.database_schema_version:
            raise DataManagementError("schema_incompatible")
        validate_database(database, current_schema=False)
        expected = {
            "files/originals/" + key: checksum for key, checksum in database_files(database)
        }
        if expected != {item.path: item.sha256 for item in manifest.files}:
            raise DataManagementError("missing_pdf")
        # Migrate only a disposable clone while validating an older known revision.
        with tempfile.TemporaryDirectory(dir=staging.parent) as temp:
            clone = Path(temp) / "casefile-ai.db"
            shutil.copyfile(database, clone)
            self._migrate(clone)
            counts = validate_database(clone)
            if database_files(clone) != database_files(database):
                raise DataManagementError("database_references")
        return counts

    def _migrate(self, database: Path) -> None:
        settings = self._settings.model_copy(
            update={
                "data_dir": database.parent,
                "database_url": f"sqlite:///{database.as_posix()}",
            }
        )
        migrate_database(settings)
        if database_revision(database) != self._head:
            raise DataManagementError("schema_incompatible")

    def restore(self, staging: Path, destination: Path) -> dict[str, int]:
        try:
            manifest = BackupManifest.model_validate_json((staging / "manifest.json").read_bytes())
            counts = self._validate_staged(staging, manifest)
            destination.mkdir(parents=True, exist_ok=False)
            incomplete = destination / "RESTORE_INCOMPLETE"
            incomplete.write_text("恢复未完成；不得作为工作数据目录使用。", encoding="utf-8")
            database = destination / "casefile-ai.db"
            shutil.copyfile(staging / DATABASE_ENTRY, database)
            originals = destination / "originals"
            originals.mkdir()
            for item in manifest.files:
                shutil.copyfile(staging / item.path, originals / PurePosixPath(item.path).name)
            self._migrate(database)
            if validate_database(database) != counts:
                raise DataManagementError("database_references")
            for key, checksum in database_files(database):
                if sha256(storage_path(originals, key)) != checksum:
                    raise DataManagementError("pdf_checksum")
            (destination / "backup_manifest.json").write_text(
                manifest.model_dump_json(indent=2), encoding="utf-8"
            )
            incomplete.unlink()
            return counts
        except DataManagementError:
            raise
        except Exception:
            raise DataManagementError("restore_failed") from None
