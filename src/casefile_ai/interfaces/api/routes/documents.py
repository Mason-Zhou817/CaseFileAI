"""Thin HTTP adapters over the shared ingestion service."""

from collections.abc import Callable
from typing import Annotated

from fastapi import APIRouter, File, Form, Path, UploadFile
from fastapi.responses import FileResponse

from casefile_ai.application.document_contracts import DocumentPage, DocumentVersion
from casefile_ai.application.services.documents import DefaultIngestionService
from casefile_ai.interfaces.api.service_resolver import service_resolver


def build_document_router(
    service: DefaultIngestionService | None = None,
    *,
    factory: Callable[[], DefaultIngestionService] | None = None,
) -> APIRouter:
    resolve = service_resolver(service, factory)
    router = APIRouter(prefix="/api", tags=["documents"])

    @router.post("/cases/{case_id}/documents", response_model=DocumentVersion)
    def upload(
        case_id: str,
        file: Annotated[UploadFile, File()],
        document_id: Annotated[str | None, Form()] = None,
    ) -> DocumentVersion:
        return resolve().upload_document(case_id, file.file, file.filename or "", document_id)

    @router.get("/cases/{case_id}/documents")
    def list_documents(case_id: str) -> list[DocumentVersion]:
        return resolve().list_documents(case_id)

    @router.get("/documents/{document_id}")
    def detail(document_id: str) -> dict[str, object]:
        versions = resolve().versions(document_id)
        return {
            "document_id": document_id,
            "case_id": versions[0].case_id,
            "latest": versions[0],
            "versions": versions,
        }

    @router.get("/documents/{document_id}/pages/{page_number}")
    def page(
        document_id: str,
        page_number: Annotated[int, Path(ge=1)],
        document_version_id: str | None = None,
    ) -> DocumentPage:
        return resolve().get_page(document_id, page_number, document_version_id)

    @router.get("/documents/{document_id}/original")
    def original(document_id: str, document_version_id: str | None = None) -> FileResponse:
        path, filename = resolve().original(document_id, document_version_id)
        return FileResponse(path, media_type="application/pdf", filename=filename)

    return router
