"""Additive lifecycle, backup and export contracts; frozen source IDs stay unchanged."""

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field

from casefile_ai.application.contracts import CaseSummary
from casefile_ai.application.document_contracts import DocumentVersion


@dataclass(frozen=True)
class ManagedCase:
    case_id: str
    name: str
    description: str | None
    status: Literal["active", "archived"]


class LifecycleRepository(Protocol):
    def managed_list(self, include_archived: bool) -> list[ManagedCase]: ...
    def managed_get(self, case_id: str) -> ManagedCase: ...
    def update_case(
        self, case_id: str, *, name: str | None = None, archived: bool | None = None
    ) -> ManagedCase: ...


class DataManagementError(RuntimeError):
    def __init__(self, category: str) -> None:
        self.category = category
        super().__init__(category)


class ClosedModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class BackupFile(ClosedModel):
    path: str
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    size: int = Field(ge=0)


class BackupManifest(ClosedModel):
    backup_format_version: int
    application_version: str
    created_at: str
    database_schema_version: str
    database: BackupFile
    files: list[BackupFile]
    total_file_count: int = Field(ge=1)
    total_size: int = Field(ge=0)


class Artifact(ClosedModel):
    artifact_id: str
    filename: str
    size: int
    kind: Literal["backup", "json", "markdown"]


class RestorePreview(ClosedModel):
    restore_id: str
    counts: dict[str, int]
    source_schema_version: str
    restored_schema_version: str
    total_size: int
    message: str = "备份已验证。确认后仅创建新的恢复副本，不覆盖或切换当前工作数据。"


class RestoreResult(ClosedModel):
    restore_id: str
    data_directory: str
    database_path: str
    counts: dict[str, int]
    message: str = "恢复副本已验证完成。当前工作数据未被覆盖或切换。"


class ExportDocument(ClosedModel):
    document_id: str
    filename: str
    current_version: int
    versions: list[DocumentVersion]
    pages: list[dict[str, Any]]


class AnalysisExport(ClosedModel):
    metadata: dict[str, Any]
    case: ManagedCase
    documents: list[ExportDocument]
    answers: list[dict[str, Any]]
    claims: list[dict[str, Any]]
    coverage: list[dict[str, Any]]
    candidates: list[dict[str, Any]]
    contradictions: list[dict[str, Any]]
    timeline: list[dict[str, Any]]
    feedback_summary: dict[str, Any] = Field(default_factory=dict)
    notices: list[str]


class BackupBackend(Protocol):
    def create(self, destination: Path) -> BackupManifest: ...
    def validate(self, archive: Path, staging: Path) -> tuple[BackupManifest, dict[str, int]]: ...
    def restore(self, staging: Path, destination: Path) -> dict[str, int]: ...


class ExportReader(Protocol):
    def read(self, case_id: str) -> AnalysisExport: ...


def managed_summary(case: CaseSummary) -> ManagedCase:
    return ManagedCase(case.case_id, case.name, case.description, "active")
