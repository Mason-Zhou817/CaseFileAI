"""Explicit, closed public diagnostics schema; never serialize Settings."""

from typing import Literal

from pydantic import BaseModel, ConfigDict


class StatusModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ApplicationStatus(StatusModel):
    version: str


class DatabaseStatus(StatusModel):
    configured: bool
    reachable: bool
    schema_status: Literal["current", "outdated", "missing", "unavailable", "unknown"]
    path: str | None
    revision: str | None
    expected_revision: str | None


class DirectoryStatus(StatusModel):
    path: str
    exists: bool
    writable: bool


class EmbeddingStatus(StatusModel):
    configured: bool
    model: str
    revision: str | None
    dependency_available: bool
    model_cached: bool
    available: bool
    loaded: bool


class OcrStatus(StatusModel):
    dependency_available: bool
    model_available: bool


class ProviderStatus(StatusModel):
    configured: bool
    enabled: bool
    connection_verified: bool


class LogsStatus(StatusModel):
    directory: str
    file_logging_available: bool


class SystemStatus(StatusModel):
    status: Literal["ready", "degraded"]
    configuration_file: str | None = None
    application: ApplicationStatus
    database: DatabaseStatus
    data_directory: DirectoryStatus
    embedding: EmbeddingStatus
    ocr: OcrStatus
    qa_provider: ProviderStatus
    logs: LogsStatus
    notices: list[str]
