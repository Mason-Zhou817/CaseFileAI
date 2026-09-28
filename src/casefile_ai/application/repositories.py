"""Persistence protocols required by application services."""

from __future__ import annotations

from typing import Protocol

from casefile_ai.application.contracts import CaseSummary


class CaseRepository(Protocol):
    def add(self, case: CaseSummary) -> CaseSummary: ...

    def list(self) -> list[CaseSummary]: ...

    def get(self, case_id: str) -> CaseSummary | None: ...
