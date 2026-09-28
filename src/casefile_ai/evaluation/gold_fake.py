"""An oracle test double: answers only when every annotated quote was retrieved.

This measures the retrieval/citation pipeline, not real-model answer quality.
"""

from __future__ import annotations

import json
from typing import Any

from casefile_ai.application.qa_contracts import (
    ModelAnswerAlignment,
    ModelGroundedAnswer,
    ModelSupportDecision,
)
from casefile_ai.evaluation.runner import Question, _normalized


class GoldEvidenceFakeLLM:
    model_version = "mvp-v1-gold-evidence-fake-v2"

    def __init__(self, questions: list[Question]) -> None:
        self._questions = {item.question: item for item in questions}
        self._active: Question | None = None

    def generate_structured(self, *, system_prompt: str, user_prompt: str,
                            response_model: type[Any]) -> Any:
        payload = json.loads(user_prompt)
        if response_model is ModelAnswerAlignment:
            # An explicit gold oracle, not a claim of real semantic judge quality.
            question = self._questions[payload["question"]]
            cited = {alias for claim in payload["claims"] for alias in claim["citation_ids"]}
            sources = [item for item in payload["evidence"] if item["citation_id"] in cited]
            answer_matches = any(_normalized(value) in _normalized(payload["answer"])
                                 for value in question.expected_answer_any)
            scope_matches = all(any(item["page_number"] == gold.page and
                                    _normalized(gold.snippet) in _normalized(item["quote"])
                                    for item in sources) for gold in question.gold_evidence)
            return response_model.model_validate({
                "requested_fact": question.question, "requested_scope": "annotated gold sources",
                "answers_requested_fact": "matches" if answer_matches else "mismatch",
                "field_matches": "matches" if answer_matches else "mismatch",
                "source_scope_matches": "matches" if scope_matches else "mismatch",
                "reason": "offline gold answer and cited page/snippet gate, not semantic judging",
            })
        if response_model is ModelSupportDecision:
            assert self._active is not None
            evidence = payload["evidence"]
            supported = all(
                any(_normalized(gold.snippet) in _normalized(item["quote"])
                    for item in evidence)
                for gold in self._active.gold_evidence
            )
            return response_model.model_validate({
                "verdict": "supported" if supported else "unsupported",
                "reason": "synthetic gold quote gate",
            })
        assert issubclass(response_model, ModelGroundedAnswer)
        question = self._questions[payload["question"]]
        self._active = question
        if question.expected_status == "insufficient_evidence":
            return response_model.model_validate({
                "status": "insufficient_evidence", "answer": "", "claims": [],
            })
        selected: list[str] = []
        for gold in question.gold_evidence:
            match = next(
                (item for item in payload["evidence"]
                 if item["page_number"] == gold.page
                 and _normalized(gold.snippet) in _normalized(item["quote"])),
                None,
            )
            if match is None:
                return response_model.model_validate({
                    "status": "insufficient_evidence", "answer": "", "claims": [],
                })
            if match["citation_id"] not in selected:
                selected.append(match["citation_id"])
        answer = question.expected_answer_any[0]
        return response_model.model_validate({
            "status": "answered", "answer": answer,
            "claims": [{"text": answer, "citation_ids": selected, "confidence": 1.0}],
        })
