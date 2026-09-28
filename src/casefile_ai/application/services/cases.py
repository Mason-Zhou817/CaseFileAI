"""Case management use cases shared by API, CLI, and MCP."""

from __future__ import annotations

from casefile_ai.application.contracts import CaseSummary
from casefile_ai.application.repositories import CaseRepository
from casefile_ai.domain.exceptions import CaseNotFoundError, InvalidCaseError
from casefile_ai.domain.ids import new_id


def normalize_case_name(name: str) -> str:
    normalized_name = name.strip()
    if not normalized_name:
        raise InvalidCaseError("case name must not be blank")
    if len(normalized_name) > 200:
        raise InvalidCaseError("case name must not exceed 200 characters")
    return normalized_name


class DefaultCaseService:
    def __init__(self, repository: CaseRepository) -> None:
        self._repository = repository

    def create_case(self, name: str, description: str | None = None) -> CaseSummary:
        normalized_name = normalize_case_name(name)

        normalized_description = description.strip() if description else None
        if normalized_description == "":
            normalized_description = None
        if normalized_description is not None and len(normalized_description) > 5_000:
            raise InvalidCaseError("case description must not exceed 5000 characters")

        return self._repository.add(
            CaseSummary(
                case_id=new_id(),
                name=normalized_name,
                description=normalized_description,
            )
        )

    def list_cases(self) -> list[CaseSummary]:
        return self._repository.list()

    def get_case(self, case_id: str) -> CaseSummary:
        case = self._repository.get(case_id)
        if case is None:
            raise CaseNotFoundError(case_id)
        return case
