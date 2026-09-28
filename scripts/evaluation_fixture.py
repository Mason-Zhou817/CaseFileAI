"""Synthetic citation-bound fixtures shared by optional evaluation scripts."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from casefile_ai.application.claim_contracts import ConflictCandidate, ExtractedClaim
from casefile_ai.application.contracts import AnswerCitation
from casefile_ai.application.contradiction_contracts import ContradictionResult
from casefile_ai.infrastructure.llm.openai_compatible import OpenAICompatibleLLMProvider


class SingleCandidate:
    def __init__(self, candidate: ConflictCandidate) -> None:
        self.candidate = candidate

    def generate(
        self, case_id: str, extraction_version: str, model_version: str
    ) -> tuple[ConflictCandidate, ...]:
        return (self.candidate,)


class ProbeRepository:
    def get_result(self, result_id: str) -> ContradictionResult | None:
        return None

    def save_result(self, result: ContradictionResult) -> ContradictionResult:
        return result

    def list_case_results(self, case_id: str) -> tuple[ContradictionResult, ...]:
        return ()


class RecordingLLM:
    def __init__(self, provider: OpenAICompatibleLLMProvider) -> None:
        self.provider = provider
        self.output: dict[str, Any] | None = None

    @property
    def model_version(self) -> str:
        return self.provider.model_version

    def generate_structured(
        self, *, system_prompt: str, user_prompt: str, response_model: type[BaseModel]
    ) -> BaseModel:
        result = self.provider.generate_structured(
            system_prompt=system_prompt, user_prompt=user_prompt, response_model=response_model
        )
        self.output = result.model_dump()
        return result


def _candidate(name: str, left: str, right: str, kind: str) -> ConflictCandidate:
    claims = tuple(
        ExtractedClaim(
            f"{name}-c{i}", f"{name}-a{i}", f"{name}-s{i}", text,
            kind, (f"{name}-e{i}",), "synthetic-v1", "synthetic-claim-v1",
        )
        for i, text in enumerate((left, right), start=1)
    )
    citations = tuple(
        AnswerCitation(
            f"{name}-e{i}", f"{name}-s{i}", f"{name}-doc{i}",
            f"{name}-page{i}", "合成材料.pdf", 1, text,
        )
        for i, text in enumerate((left, right), start=1)
    )
    return ConflictCandidate(
        "synthetic-case", claims[0], claims[1], (citations[0],),
        (citations[1],), "contract" if kind == "person" else "receipt",
    )
