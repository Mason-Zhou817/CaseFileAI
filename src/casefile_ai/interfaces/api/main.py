"""FastAPI application factory."""

from __future__ import annotations

import logging
import os
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from importlib.resources import files
from pathlib import Path
from typing import cast
from uuid import uuid4

from fastapi import FastAPI, Request, status
from fastapi.responses import FileResponse, JSONResponse
from starlette.responses import Response

from casefile_ai import __version__
from casefile_ai.application.bootstrap import (
    ApplicationRuntime,
    build_application_runtime,
    prepare_local_storage,
)
from casefile_ai.application.claim_contracts import ClaimExtractionError
from casefile_ai.application.contracts import CaseService
from casefile_ai.application.contradiction_contracts import ContradictionValidationError
from casefile_ai.application.data_contracts import DataManagementError
from casefile_ai.application.data_messages import DATA_MESSAGES
from casefile_ai.application.document_contracts import DocumentNotFoundError, InvalidDocumentError
from casefile_ai.application.material_contracts import (
    IndexInProgressError,
    IndexOperationError,
    MaterialsStatus,
)
from casefile_ai.application.qa_contracts import InvalidQuestionError, QAUnavailableError
from casefile_ai.application.retrieval_contracts import InvalidSearchError, RetrievalIndexError
from casefile_ai.application.services.case_lifecycle import CaseLifecycleService
from casefile_ai.application.services.data_management import DataManagementService
from casefile_ai.application.services.documents import DefaultIngestionService
from casefile_ai.application.services.materials import MaterialsService
from casefile_ai.application.services.qa import DefaultQAService
from casefile_ai.application.services.retrieval import DefaultRetrievalService
from casefile_ai.application.services.system_status import SystemStatusService
from casefile_ai.application.services.workspace import CaseWorkspaceService
from casefile_ai.application.system_contracts import SystemStatus
from casefile_ai.config import Settings
from casefile_ai.domain.exceptions import CaseNotFoundError, InvalidCaseError
from casefile_ai.infrastructure.llm.errors import MESSAGES, ProviderError, provider_failure
from casefile_ai.infrastructure.safe_logging import SafeLogging, safe_event
from casefile_ai.interfaces.api.routes.cases import build_case_router
from casefile_ai.interfaces.api.routes.data_management import build_data_router
from casefile_ai.interfaces.api.routes.documents import build_document_router
from casefile_ai.interfaces.api.routes.qa import build_qa_router
from casefile_ai.interfaces.api.routes.retrieval import build_retrieval_router
from casefile_ai.interfaces.api.routes.workspace import build_workspace_router


def create_app(
    case_service: CaseService | None = None,
    ingestion_service: DefaultIngestionService | None = None,
    retrieval_service: DefaultRetrievalService | None = None,
    qa_service: DefaultQAService | None = None,
    workspace_service: CaseWorkspaceService | None = None,
    *,
    settings: Settings | None = None,
    system_status_service: SystemStatusService | None = None,
    materials_service: MaterialsService | None = None,
    lifecycle_service: CaseLifecycleService | None = None,
    data_management_service: DataManagementService | None = None,
    log_manager: SafeLogging | None = None,
    startup_report: Callable[[SystemStatus], None] | None = None,
    instance_id: str | None = None,
) -> FastAPI:
    production = case_service is None
    configured = settings or (Settings() if production else None)

    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        runtime: ApplicationRuntime | None = None
        owned_logs: SafeLogging | None = None
        try:
            if production:
                assert configured is not None
                logs = log_manager
                if logs is None:
                    owned_logs = logs = SafeLogging(configured.log_dir, configured.log_level)
                try:
                    prepare_local_storage(configured)
                except Exception:
                    # Keep the diagnostic GUI reachable; CLI preflight is stricter.
                    safe_event("startup", "degraded", level=logging.ERROR)
                runtime = build_application_runtime(configured, logs)
                application.state.runtime = runtime
                application.state.database_engine = runtime.cases.engine
                if startup_report is not None:
                    startup_report(runtime.diagnostics.snapshot())
                safe_event("startup")
            yield
        except Exception:
            safe_event("startup", "failed", level=logging.ERROR)
            raise RuntimeError("application_lifespan_failed") from None
        finally:
            try:
                if runtime is not None:
                    runtime.close()
                    safe_event("shutdown")
            finally:
                if owned_logs is not None:
                    owned_logs.close()

    app = FastAPI(title="CaseFile AI", version=__version__, lifespan=lifespan)
    identity = instance_id or str(uuid4())

    @app.get("/api/system/identity", include_in_schema=False)
    def application_identity() -> dict[str, str | int]:
        return {
            "application": "casefile-ai",
            "version": __version__,
            "instance_id": identity,
            "pid": os.getpid(),
        }

    @app.middleware("http")
    async def operation_log(
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        started = time.perf_counter()
        outcome = "failed"
        try:
            response = await call_next(request)
            outcome = "ok" if response.status_code < 400 else "failed"
            return response
        finally:
            safe_event("api", outcome, duration_ms=(time.perf_counter() - started) * 1000)

    def runtime_services() -> ApplicationRuntime:
        return cast(ApplicationRuntime, app.state.runtime)

    def production_qa() -> DefaultQAService:
        runtime = runtime_services()
        runtime.require_model()
        return runtime.qa

    @app.get("/", include_in_schema=False)
    def gui() -> FileResponse:
        return FileResponse(Path(str(files("casefile_ai") / "interfaces/web/index.html")))

    @app.get("/assets/workspace.js", include_in_schema=False)
    def gui_script() -> FileResponse:
        return FileResponse(
            Path(str(files("casefile_ai") / "interfaces/web/workspace.js")),
            media_type="text/javascript",
        )

    @app.get("/assets/app.css", include_in_schema=False)
    def gui_style() -> FileResponse:
        return FileResponse(
            Path(str(files("casefile_ai") / "interfaces/web/app.css")), media_type="text/css"
        )

    @app.exception_handler(CaseNotFoundError)
    async def case_not_found_handler(
        _request: Request, exception: CaseNotFoundError
    ) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_404_NOT_FOUND,
            content={"detail": str(exception)},
        )

    @app.exception_handler(InvalidCaseError)
    async def invalid_case_handler(_request: Request, exception: InvalidCaseError) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            content={"detail": str(exception)},
        )

    if production:
        app.include_router(
            build_case_router(
                factory=lambda: runtime_services().cases.service,
                lifecycle_factory=lambda: runtime_services().lifecycle,
            )
        )
        app.include_router(build_data_router(lambda: runtime_services().data_management))
        app.include_router(build_document_router(factory=lambda: runtime_services().ingestion))
        app.include_router(build_retrieval_router(factory=lambda: runtime_services().retrieval))
        app.include_router(build_qa_router(factory=production_qa))
        app.include_router(
            build_workspace_router(
                factory=lambda: runtime_services().workspace,
                require_model=lambda: runtime_services().require_model(),
                require_semantic_model=lambda case_id: runtime_services().require_semantic_model(
                    case_id
                ),
            )
        )
    else:
        app.include_router(
            build_case_router(
                case_service,
                lifecycle_factory=(lambda: lifecycle_service)
                if lifecycle_service is not None
                else None,
            )
        )
        if data_management_service is not None:
            app.include_router(build_data_router(lambda: data_management_service))
        if ingestion_service is not None:
            app.include_router(build_document_router(ingestion_service))
        if retrieval_service is not None:
            app.include_router(build_retrieval_router(retrieval_service))
        if qa_service is not None:
            app.include_router(build_qa_router(qa_service))
        if workspace_service is not None:
            app.include_router(build_workspace_router(workspace_service))

    def model_error(exception: ProviderError) -> JSONResponse:
        safe_event(
            "provider",
            exception.category,
            diagnostic_id=exception.diagnostic_id,
            level=logging.ERROR,
        )
        return JSONResponse(
            status_code=503,
            content={"detail": MESSAGES[exception.category], "error": exception.safe_fields()},
        )

    @app.exception_handler(DataManagementError)
    async def data_error(_request: Request, exception: DataManagementError) -> JSONResponse:
        category = (
            exception.category if exception.category in DATA_MESSAGES else "data_management_failed"
        )
        diagnostic_id = str(uuid4())
        safe_event("data_management", category, diagnostic_id=diagnostic_id, level=logging.ERROR)
        code = 422
        if category in {"artifact_not_found", "restore_not_found"}:
            code = 404
        elif category in {"restore_already_created", "backup_busy"}:
            code = 409
        elif category in {"backup_failed", "restore_failed", "data_management_failed"}:
            code = 500
        return JSONResponse(
            status_code=code,
            content={
                "detail": DATA_MESSAGES[category],
                "error": {"category": category, "diagnostic_id": diagnostic_id},
            },
        )

    @app.exception_handler(ProviderError)
    async def provider_error(_request: Request, exception: ProviderError) -> JSONResponse:
        return model_error(exception)

    @app.exception_handler(DocumentNotFoundError)
    async def document_not_found(
        _request: Request, exception: DocumentNotFoundError
    ) -> JSONResponse:
        return JSONResponse(status_code=404, content={"detail": str(exception)})

    @app.exception_handler(InvalidDocumentError)
    async def invalid_document(_request: Request, exception: InvalidDocumentError) -> JSONResponse:
        return JSONResponse(status_code=422, content={"detail": str(exception)})

    @app.exception_handler(InvalidSearchError)
    async def invalid_search(_request: Request, exception: InvalidSearchError) -> JSONResponse:
        return JSONResponse(status_code=422, content={"detail": str(exception)})

    @app.exception_handler(RetrievalIndexError)
    async def retrieval_index_error(
        _request: Request, exception: RetrievalIndexError
    ) -> JSONResponse:
        diagnostic_id = (
            exception.diagnostic_id if isinstance(exception, IndexOperationError) else str(uuid4())
        )
        safe_event("index", "failed", diagnostic_id=diagnostic_id, level=logging.ERROR)
        return JSONResponse(
            status_code=500,
            content={
                "detail": "索引处理失败，已导入材料仍保留。请重试并查看系统状态。",
                "error": {"category": "index_failed", "diagnostic_id": diagnostic_id},
            },
        )

    @app.exception_handler(IndexInProgressError)
    async def index_processing(_request: Request, exception: IndexInProgressError) -> JSONResponse:
        return JSONResponse(
            status_code=409,
            content={
                "detail": "当前案件正在建立索引，请等待完成。",
                "error": {"category": "index_processing"},
            },
        )

    @app.exception_handler(InvalidQuestionError)
    async def invalid_question(_request: Request, exception: InvalidQuestionError) -> JSONResponse:
        return JSONResponse(status_code=422, content={"detail": str(exception)})

    @app.exception_handler(QAUnavailableError)
    async def qa_unavailable(_request: Request, exception: QAUnavailableError) -> JSONResponse:
        failure = provider_failure(exception)
        if failure is not None:
            return model_error(failure)
        safe_event("provider", "unknown_provider_error", level=logging.ERROR)
        return JSONResponse(status_code=503, content={"detail": "QA model service unavailable"})

    @app.exception_handler(ClaimExtractionError)
    async def claim_error(_request: Request, exception: ClaimExtractionError) -> JSONResponse:
        failure = provider_failure(exception)
        if failure is not None:
            return model_error(failure)
        return JSONResponse(status_code=422, content={"detail": str(exception)})

    @app.exception_handler(ContradictionValidationError)
    async def contradiction_error(
        _request: Request, exception: ContradictionValidationError
    ) -> JSONResponse:
        return JSONResponse(status_code=422, content={"detail": str(exception)})

    @app.exception_handler(Exception)
    async def unexpected_error(_request: Request, exception: Exception) -> JSONResponse:
        diagnostic_id = str(uuid4())
        safe_event("api", "unhandled_error", diagnostic_id=diagnostic_id, level=logging.ERROR)
        return JSONResponse(
            status_code=500,
            content={
                "detail": "处理失败，请使用诊断编号查看安全日志。",
                "diagnostic_id": diagnostic_id,
            },
        )

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/api/system/status", response_model=SystemStatus)
    def system_status() -> SystemStatus:
        if system_status_service is not None:
            return system_status_service.snapshot()
        if production:
            return runtime_services().diagnostics.snapshot()
        raise ProviderError("unavailable")

    if production or materials_service is not None:

        @app.get("/api/cases/{case_id}/materials", response_model=MaterialsStatus)
        def materials_status(case_id: str) -> MaterialsStatus:
            service = (
                materials_service if materials_service is not None else runtime_services().materials
            )
            return service.snapshot(case_id)

    return app
