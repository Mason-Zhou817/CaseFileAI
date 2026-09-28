"""HTTP adapters for indexing and retrieval application services."""

from collections.abc import Callable
from typing import Annotated

from fastapi import APIRouter, Query

from casefile_ai.application.retrieval_contracts import IndexResult, RetrievalHit, SearchMode
from casefile_ai.application.services.retrieval import DefaultRetrievalService
from casefile_ai.interfaces.api.service_resolver import service_resolver


def build_retrieval_router(
    service: DefaultRetrievalService | None = None,
    *,
    factory: Callable[[], DefaultRetrievalService] | None = None,
) -> APIRouter:
    resolve = service_resolver(service, factory)
    router = APIRouter(prefix="/api/cases/{case_id}", tags=["retrieval"])

    @router.post("/index", response_model=IndexResult)
    def index_case(case_id: str) -> IndexResult:
        return resolve().index_case(case_id)

    @router.get("/search", response_model=list[RetrievalHit])
    def search(
        case_id: str,
        q: Annotated[str, Query(min_length=1, max_length=1000)],
        mode: SearchMode = "hybrid",
        limit: Annotated[int, Query(ge=1, le=50)] = 10,
    ) -> list[RetrievalHit]:
        return resolve().search(case_id, q, mode, limit)

    return router
