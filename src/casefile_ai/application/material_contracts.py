"""Additive material readiness contracts; no changes to retrieval or QA contracts."""

from dataclasses import dataclass
from typing import Literal, Protocol

from casefile_ai.application.document_contracts import DocumentVersion
from casefile_ai.application.retrieval_contracts import RetrievalIndexError

IndexStatus = Literal["not_indexed", "ready", "stale", "processing", "failed"]


@dataclass(frozen=True)
class VersionIndexFacts:
    version: DocumentVersion
    signature: str
    complete: bool
    has_index: bool
    attempt: str | None
    diagnostic_id: str | None


@dataclass(frozen=True)
class MaterialStatus:
    version: DocumentVersion
    status: IndexStatus
    label: str
    message: str
    diagnostic_id: str | None = None


@dataclass(frozen=True)
class MaterialsStatus:
    case_id: str
    documents: list[MaterialStatus]
    status: IndexStatus
    label: str
    message: str
    action_label: str
    incomplete_reasons: list[IndexStatus]


class MaterialRepository(Protocol):
    def facts(self, case_id: str, embedding_version: str) -> list[VersionIndexFacts]: ...

    def record_attempt(
        self, facts: list[VersionIndexFacts], outcome: str, diagnostic_id: str | None
    ) -> None: ...


class IndexInProgressError(RuntimeError):
    pass


class IndexOperationError(RetrievalIndexError):
    def __init__(self, diagnostic_id: str) -> None:
        self.diagnostic_id = diagnostic_id
        super().__init__("index_failed")
