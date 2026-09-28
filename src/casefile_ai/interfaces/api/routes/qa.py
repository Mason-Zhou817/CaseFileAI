"""HTTP adapter for the shared grounded QA application service."""

from collections.abc import Callable
from dataclasses import asdict

from fastapi import APIRouter

from casefile_ai.application.services.qa import DefaultQAService
from casefile_ai.interfaces.api.schemas import AskCaseRequest, AskCaseResponse
from casefile_ai.interfaces.api.service_resolver import service_resolver


def build_qa_router(
    service: DefaultQAService | None = None,
    *,
    factory: Callable[[], DefaultQAService] | None = None,
) -> APIRouter:
    resolve = service_resolver(service, factory)
    router = APIRouter(prefix="/api/cases/{case_id}", tags=["qa"])

    @router.post("/ask", response_model=AskCaseResponse)
    def ask(case_id: str, payload: AskCaseRequest) -> AskCaseResponse:
        result = resolve().ask_case(case_id, payload.question, payload.retrieval_limit)
        if result.metadata is None:
            raise RuntimeError("QA service returned no run metadata")
        return AskCaseResponse.model_validate(
            {
                "answer_id": result.answer_id,
                "status": "insufficient_evidence" if result.abstained else "answered",
                "question": result.question,
                "answer": result.answer,
                "claims": [asdict(claim) for claim in result.claims],
                "citations": [asdict(citation) for citation in result.citations],
                "metadata": asdict(result.metadata),
            }
        )

    return router
