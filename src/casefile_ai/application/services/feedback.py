"""Explicit local feedback and human-gated evaluation promotion."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Literal

from pydantic import ValidationError

from casefile_ai.application.feedback_contracts import FeedbackError, FeedbackRepository

RATINGS = ("correct", "partially_correct", "incorrect", "insufficient_evidence")
CATEGORIES = ("retrieval", "answer", "citation", "claim", "other")


class FeedbackService:
    def __init__(self, repository: FeedbackRepository) -> None:
        self._repository = repository

    def submit_feedback(
        self, case_id: str, answer_id: str, rating: str, category: str, note: str = ""
    ) -> dict[str, Any]:
        if rating not in RATINGS:
            raise FeedbackError("invalid_rating")
        if category not in CATEGORIES:
            raise FeedbackError("invalid_category")
        if len(note) > 2000:
            raise FeedbackError("note_too_long")
        return self._repository.add(case_id, answer_id, rating, category, note)

    def list_feedback(self, case_id: str | None = None) -> list[dict[str, Any]]:
        return self._repository.list(case_id)

    def get_feedback_summary(self, case_id: str | None = None) -> dict[str, Any]:
        rows = self.list_feedback(case_id)
        return {
            "total": len(rows),
            "rating": {value: sum(row["rating"] == value for row in rows) for value in RATINGS},
            "category": {
                value: sum(row["category"] == value for row in rows) for value in CATEGORIES
            },
            "incorrect_by_category": {
                value: sum(row["rating"] == "incorrect" and row["category"] == value
                           for row in rows) for value in CATEGORIES
            },
            "review_status": {
                value: sum(row["review_status"] == value for row in rows)
                for value in ("pending", "approved", "rejected")
            },
        }

    def export_feedback_review_dataset(self, destination: Path, case_id: str | None = None) -> int:
        rows = [row for row in self._repository.review_rows(case_id)
                if row["review_status"] == "pending"]
        self._write_jsonl(destination, rows)
        return len(rows)

    def review_feedback(
        self,
        feedback_id: str,
        status: Literal["approved", "rejected"],
        expected_status: str | None = None,
        expected_answer: str | None = None,
        expected_evidence: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        if status not in ("approved", "rejected"):
            raise FeedbackError("invalid_review_status")
        if status == "approved":
            self._sample(feedback_id, "", expected_status, expected_answer, expected_evidence)
        else:
            expected_status = expected_answer = None
            expected_evidence = None
        return self._repository.review(
            feedback_id, status, expected_status, expected_answer, expected_evidence
        )

    @staticmethod
    def _sample(
        feedback_id: str,
        question: str,
        expected_status: str | None,
        expected_answer: str | None,
        expected_evidence: list[dict[str, Any]] | None,
    ) -> dict[str, Any]:
        from casefile_ai.evaluation.runner import Question

        if expected_status is None or expected_evidence is None:
            raise FeedbackError("incomplete_gold")
        try:
            sample = Question.model_validate(
                {
                    "id": f"feedback-{feedback_id}",
                    "question": question or "validation placeholder",
                    "expected_status": expected_status,
                    "expected_answer_any": [expected_answer] if expected_answer else [],
                    "gold_evidence": expected_evidence,
                    "tags": ["feedback-reviewed"],
                }
            )
        except ValidationError:
            raise FeedbackError("incomplete_gold") from None
        return sample.model_dump(mode="json")

    def export_approved_eval_dataset(
        self, destination: Path, case_set_path: Path, case_id: str
    ) -> int:
        from casefile_ai.evaluation.runner import CaseSet

        try:
            case_set = CaseSet.model_validate_json(case_set_path.read_text(encoding="utf-8"))
        except (OSError, ValidationError):
            raise FeedbackError("invalid_case_set") from None
        keys = {document.doc_key for document in case_set.documents}
        rows = self._repository.review_rows(case_id)
        samples: list[dict[str, Any]] = []
        for row in rows:
            if row["review_status"] != "approved":
                continue
            sample = self._sample(
                row["feedback_id"],
                row["question"],
                row["expected_status"],
                row["expected_answer"],
                row["expected_evidence"],
            )
            if {item["doc_key"] for item in sample["gold_evidence"]} - keys:
                raise FeedbackError("unknown_doc_key")
            samples.append(sample)
        if not samples:
            raise FeedbackError("no_approved_feedback")
        self._write_jsonl(destination, samples)
        return len(samples)

    @staticmethod
    def _write_jsonl(destination: Path, rows: list[dict[str, Any]]) -> None:
        with destination.open("x", encoding="utf-8") as target:
            for row in rows:
                target.write(json.dumps(row, ensure_ascii=False) + "\n")
