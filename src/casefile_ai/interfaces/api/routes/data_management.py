"""Thin local data-management adapters with ID-only downloads and two-step restore."""

from collections.abc import Callable
from typing import Annotated, Literal

from fastapi import APIRouter, File, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict

from casefile_ai.application.data_contracts import Artifact, RestorePreview, RestoreResult
from casefile_ai.application.services.data_management import DataManagementService


class RestoreConfirmation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    confirmation: Literal["create_new_copy"]


def build_data_router(factory: Callable[[], DataManagementService]) -> APIRouter:
    router = APIRouter(prefix="/api", tags=["data-management"])

    def artifact_response(artifact: Artifact) -> dict[str, object]:
        return {
            **artifact.model_dump(),
            "download_url": f"/api/data/artifacts/{artifact.artifact_id}",
        }

    @router.post("/data/backups")
    def backup() -> dict[str, object]:
        return artifact_response(factory().create_backup())

    @router.post("/data/restores/preview", response_model=RestorePreview)
    def restore_preview(file: Annotated[UploadFile, File()]) -> RestorePreview:
        return factory().prepare_restore(file.file)

    @router.post("/data/restores/{restore_id}/confirm", response_model=RestoreResult)
    def restore_confirm(restore_id: str, payload: RestoreConfirmation) -> RestoreResult:
        return factory().confirm_restore(restore_id, payload.confirmation)

    @router.post("/cases/{case_id}/exports/{format}")
    def export(case_id: str, format: Literal["json", "markdown"]) -> dict[str, object]:
        return artifact_response(factory().export_analysis(case_id, format))

    @router.get("/data/artifacts/{artifact_id}")
    def download(artifact_id: str) -> FileResponse:
        path, artifact = factory().download(artifact_id)
        media = {
            "backup": "application/zip",
            "json": "application/json",
            "markdown": "text/markdown",
        }
        return FileResponse(
            path,
            media_type=media[artifact.kind],
            filename=artifact.filename,
            headers={"X-Content-Type-Options": "nosniff", "Cache-Control": "no-store"},
        )

    return router
