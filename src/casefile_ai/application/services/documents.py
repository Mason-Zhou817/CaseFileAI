"""Shared local ingestion workflow; no model or retrieval calls."""

import hashlib
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import BinaryIO

import pymupdf

from casefile_ai.application.contracts import DocumentSummary, PageView
from casefile_ai.application.document_contracts import (
    DocumentPage,
    DocumentRepository,
    DocumentVersion,
    InvalidDocumentError,
    ParsedPage,
)
from casefile_ai.domain.ids import new_id
from casefile_ai.infrastructure.parsers.pdf import ExtractedPage, extract_pdf_pages

MAX_PDF_BYTES = 50 * 1024 * 1024
MAX_PAGES = 500
PARSER_VERSION = "pymupdf-rapidocr-page-v2-alnum10"


class DefaultIngestionService:
    def __init__(
        self,
        repository: DocumentRepository,
        data_dir: Path,
        parser: Callable[[Path], list[ExtractedPage]] = extract_pdf_pages,
    ) -> None:
        self._repository = repository
        self._root = data_dir.resolve() / "originals"
        self._parser = parser

    def upload_document(
        self,
        case_id: str,
        stream: BinaryIO,
        filename: str,
        document_id: str | None = None,
    ) -> DocumentVersion:
        # Filenames are metadata only; storage paths are generated stable IDs.
        filename = filename.replace("\\", "/").rsplit("/", 1)[-1]
        if not filename.lower().endswith(".pdf") or len(filename) > 255:
            raise InvalidDocumentError("a PDF filename of at most 255 characters is required")
        self._root.mkdir(parents=True, exist_ok=True)
        version_id = new_id()
        destination = self._root / f"{version_id}.pdf"
        staged: Path | None = None
        committed = False
        try:
            digest = hashlib.sha256()
            size = 0
            with tempfile.NamedTemporaryFile(
                dir=self._root, suffix=".upload", delete=False
            ) as temp:
                staged = Path(temp.name)
                while block := stream.read(1024 * 1024):
                    size += len(block)
                    if size > MAX_PDF_BYTES:
                        raise InvalidDocumentError("PDF exceeds 50 MiB import limit")
                    digest.update(block)
                    temp.write(block)
            sha256 = digest.hexdigest()
            duplicate = self._repository.find_duplicate(case_id, sha256, document_id)
            if duplicate is not None:
                return duplicate
            try:
                # Validate from memory: failed MuPDF opens can retain file handles on Windows.
                with pymupdf.open(stream=staged.read_bytes(), filetype="pdf") as pdf:  # type: ignore[no-untyped-call]
                    if not pdf.is_pdf or pdf.needs_pass or not 0 < len(pdf) <= MAX_PAGES:
                        raise InvalidDocumentError(
                            "PDF must be unencrypted and contain 1–500 pages"
                        )
                    page_count = len(pdf)
            except (pymupdf.FileDataError, pymupdf.EmptyFileError) as exc:
                raise InvalidDocumentError("invalid or empty PDF") from exc
            extracted = self._parser(staged)
            if [p.page_number for p in extracted] != list(range(1, page_count + 1)):
                raise InvalidDocumentError("parser returned inconsistent page numbering")
            pages = [
                ParsedPage(
                    new_id(),
                    page.page_number,
                    page.text,
                    hashlib.sha256(page.text.encode("utf-8")).hexdigest(),
                    page.extraction_method == "ocr",
                )
                for page in extracted
            ]
            staged.rename(destination)
            result = self._repository.save(
                case_id,
                document_id,
                version_id,
                filename,
                sha256,
                destination.name,
                PARSER_VERSION,
                pages,
            )
            committed = not result.duplicate
            return result
        finally:
            if staged is not None:
                staged.unlink(missing_ok=True)
            if not committed:
                destination.unlink(missing_ok=True)

    def import_document(self, case_id: str, path: Path) -> DocumentSummary:
        with path.open("rb") as source:
            result = self.upload_document(case_id, source, path.name)
        return DocumentSummary(
            result.document_id,
            result.case_id,
            result.filename,
            result.content_sha256,
            result.page_count,
        )

    def get_document_page(self, document_id: str, page_number: int) -> PageView:
        page = self.get_page(document_id, page_number)
        return PageView(
            page.document_id,
            page.page_id,
            page.page_number,
            page.filename,
            page.text,
            page.ocr_used,
        )

    def list_documents(self, case_id: str) -> list[DocumentVersion]:
        return self._repository.list_documents(case_id)

    def versions(self, document_id: str) -> list[DocumentVersion]:
        return self._repository.versions(document_id)

    def get_page(
        self,
        document_id: str,
        page_number: int,
        version_id: str | None = None,
    ) -> DocumentPage:
        return self._repository.page(document_id, page_number, version_id)

    def original(self, document_id: str, version_id: str | None = None) -> tuple[Path, str]:
        key, filename = self._repository.storage_key(document_id, version_id)
        path = (self._root / key).resolve()
        if path.parent != self._root or not path.is_file():
            raise InvalidDocumentError("stored original is unavailable")
        return path, filename
