"""Shared feedback repository contract and safe validation error."""

from __future__ import annotations

import builtins
from typing import Any, Protocol


class FeedbackError(ValueError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class FeedbackRepository(Protocol):
    def add(
        self, case_id: str, answer_id: str, rating: str, category: str, note: str
    ) -> dict[str, Any]: ...

    def list(self, case_id: str | None = None) -> builtins.list[dict[str, Any]]: ...

    def review(
        self,
        feedback_id: str,
        status: str,
        expected_status: str | None,
        expected_answer: str | None,
        expected_evidence: builtins.list[dict[str, Any]] | None,
    ) -> dict[str, Any]: ...

    def review_rows(self, case_id: str | None = None) -> builtins.list[dict[str, Any]]: ...
