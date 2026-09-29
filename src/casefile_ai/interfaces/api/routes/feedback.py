"""Local explicit feedback endpoints used by the browser GUI."""

from collections.abc import Callable
from typing import Any, Literal

from fastapi import APIRouter
from pydantic import BaseModel, Field

from casefile_ai.application.services.feedback import FeedbackService
from casefile_ai.interfaces.api.service_resolver import service_resolver


class FeedbackRequest(BaseModel):
    answer_id: str
    rating: Literal["correct", "partially_correct", "incorrect", "insufficient_evidence"]
    category: Literal["retrieval", "answer", "citation", "claim", "other"]
    note: str = Field(default="", max_length=2000)


def build_feedback_router(
    service: FeedbackService | None = None, *, factory: Callable[[], FeedbackService] | None = None
) -> APIRouter:
    resolve = service_resolver(service, factory)
    router = APIRouter(prefix="/api/cases/{case_id}/feedback", tags=["feedback"])

    @router.post("")
    def submit(case_id: str, payload: FeedbackRequest) -> dict[str, Any]:
        return resolve().submit_feedback(
            case_id, payload.answer_id, payload.rating, payload.category, payload.note
        )

    @router.get("/summary")
    def summary(case_id: str) -> dict[str, Any]:
        return resolve().get_feedback_summary(case_id)

    return router
