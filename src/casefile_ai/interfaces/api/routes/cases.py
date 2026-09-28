"""Case HTTP routes backed exclusively by CaseService."""

from collections.abc import Callable
from dataclasses import asdict

from fastapi import APIRouter, status

from casefile_ai.application.contracts import CaseService
from casefile_ai.application.services.case_lifecycle import CaseLifecycleService
from casefile_ai.interfaces.api.schemas import (
    ArchiveCaseRequest,
    CaseListResponse,
    CaseResponse,
    CreateCaseRequest,
    ErrorResponse,
    RenameCaseRequest,
)
from casefile_ai.interfaces.api.service_resolver import service_resolver


def build_case_router(
    service: CaseService | None = None,
    *,
    factory: Callable[[], CaseService] | None = None,
    lifecycle_factory: Callable[[], CaseLifecycleService] | None = None,
) -> APIRouter:
    resolve = service_resolver(service, factory)
    router = APIRouter(prefix="/api/cases", tags=["cases"])

    @router.post(
        "",
        response_model=CaseResponse,
        status_code=status.HTTP_201_CREATED,
        responses={422: {"model": ErrorResponse}},
    )
    def create_case(payload: CreateCaseRequest) -> CaseResponse:
        return CaseResponse.model_validate(asdict(resolve().create_case(**payload.model_dump())))

    @router.get("", response_model=CaseListResponse)
    def list_cases(include_archived: bool = False) -> CaseListResponse:
        if lifecycle_factory is not None:
            return CaseListResponse(
                cases=[
                    CaseResponse.model_validate(asdict(case))
                    for case in lifecycle_factory().list_cases(include_archived)
                ]
            )
        return CaseListResponse(
            cases=[CaseResponse.model_validate(asdict(case)) for case in resolve().list_cases()]
        )

    @router.get(
        "/{case_id}",
        response_model=CaseResponse,
        responses={404: {"model": ErrorResponse}},
    )
    def get_case(case_id: str) -> CaseResponse:
        if lifecycle_factory is not None:
            return CaseResponse.model_validate(asdict(lifecycle_factory().get_case(case_id)))
        return CaseResponse.model_validate(asdict(resolve().get_case(case_id)))

    if lifecycle_factory is not None:

        @router.patch("/{case_id}/name", response_model=CaseResponse)
        def rename(case_id: str, payload: RenameCaseRequest) -> CaseResponse:
            assert lifecycle_factory is not None
            return CaseResponse.model_validate(
                asdict(lifecycle_factory().rename_case(case_id, payload.name))
            )

        @router.patch("/{case_id}/archive", response_model=CaseResponse)
        def archive(case_id: str, payload: ArchiveCaseRequest) -> CaseResponse:
            assert lifecycle_factory is not None
            return CaseResponse.model_validate(
                asdict(lifecycle_factory().archive_case(case_id, payload.archived))
            )

    return router
