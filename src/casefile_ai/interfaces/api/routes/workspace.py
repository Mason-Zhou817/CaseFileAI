"""Thin JSON routes over the shared case workspace service."""

from collections.abc import Callable
from dataclasses import asdict
from typing import Any

from fastapi import APIRouter

from casefile_ai.application.services.workspace import CaseWorkspaceService
from casefile_ai.interfaces.api.service_resolver import service_resolver


def build_workspace_router(
    service: CaseWorkspaceService | None = None,
    *,
    factory: Callable[[], CaseWorkspaceService] | None = None,
    require_model: Callable[[], None] | None = None,
    require_semantic_model: Callable[[str], None] | None = None,
) -> APIRouter:
    resolve = service_resolver(service, factory)
    router = APIRouter(prefix="/api/cases/{case_id}", tags=["workspace"])

    @router.get("/answers")
    def answers(case_id: str) -> list[dict[str, Any]]:
        return [asdict(item) for item in resolve().answers(case_id)]

    @router.post("/answers/{answer_id}/claims")
    def extract(case_id: str, answer_id: str) -> list[dict[str, Any]]:
        if require_model is not None:
            require_model()
        return [asdict(item) for item in resolve().extract(case_id, answer_id)]

    @router.get("/answers/{answer_id}/coverage")
    def coverage(case_id: str, answer_id: str) -> dict[str, Any]:
        return asdict(resolve().coverage(case_id, answer_id))

    @router.get("/claims")
    def claims(case_id: str) -> list[dict[str, Any]]:
        return [asdict(item) for item in resolve().claims(case_id)]

    @router.get("/conflict-candidates")
    def candidates(case_id: str) -> list[dict[str, Any]]:
        return [
            {**asdict(item), "candidate_id": item.candidate_id}
            for item in resolve().candidates(case_id)
        ]

    @router.post("/contradictions")
    def detect(case_id: str) -> list[dict[str, Any]]:
        if require_semantic_model is not None:
            require_semantic_model(case_id)
        return [asdict(item) for item in resolve().detect(case_id)]

    @router.get("/contradictions")
    def results(case_id: str) -> list[dict[str, Any]]:
        return [asdict(item) for item in resolve().results(case_id)]

    @router.get("/timeline")
    def timeline(case_id: str) -> list[dict[str, Any]]:
        return [asdict(item) for item in resolve().timeline(case_id)]

    return router
