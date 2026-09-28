"""Version-aware ingestion contracts, additive to the frozen Day 1 interface."""

from dataclasses import dataclass
from typing import Protocol


class DocumentNotFoundError(LookupError):
    pass


class InvalidDocumentError(ValueError):
    pass


@dataclass(frozen=True)
class DocumentVersion:
    case_id: str
    document_id: str
    document_version_id: str
    filename: str
    content_sha256: str
    page_count: int
    version_number: int
    parser_version: str
    duplicate: bool = False


@dataclass(frozen=True)
class DocumentPage:
    case_id: str
    document_id: str
    document_version_id: str
    page_id: str
    page_number: int
    filename: str
    text: str
    text_hash: str
    ocr_used: bool


@dataclass(frozen=True)
class ParsedPage:
    page_id: str
    page_number: int
    text: str
    text_hash: str
    ocr_used: bool


class DocumentRepository(Protocol):
    def find_duplicate(
        self,
        case_id: str,
        sha256: str,
        document_id: str | None,
    ) -> DocumentVersion | None: ...

    def save(
        self,
        case_id: str,
        document_id: str | None,
        version_id: str,
        filename: str,
        sha256: str,
        storage_key: str,
        parser_version: str,
        pages: list[ParsedPage],
    ) -> DocumentVersion: ...

    def list_documents(self, case_id: str) -> list[DocumentVersion]: ...

    def versions(self, document_id: str) -> list[DocumentVersion]: ...

    def page(
        self,
        document_id: str,
        number: int,
        version_id: str | None,
    ) -> DocumentPage: ...

    def storage_key(self, document_id: str, version_id: str | None) -> tuple[str, str]: ...
