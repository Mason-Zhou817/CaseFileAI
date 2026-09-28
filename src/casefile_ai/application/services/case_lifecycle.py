"""Rename and archive without deleting or changing stable source IDs."""

from casefile_ai.application.data_contracts import LifecycleRepository, ManagedCase
from casefile_ai.application.services.cases import normalize_case_name


class CaseLifecycleService:
    def __init__(self, repository: LifecycleRepository) -> None:
        self._repository = repository

    def list_cases(self, include_archived: bool = False) -> list[ManagedCase]:
        return self._repository.managed_list(include_archived)

    def get_case(self, case_id: str) -> ManagedCase:
        return self._repository.managed_get(case_id)

    def rename_case(self, case_id: str, name: str) -> ManagedCase:
        return self._repository.update_case(case_id, name=normalize_case_name(name))

    def archive_case(self, case_id: str, archived: bool) -> ManagedCase:
        return self._repository.update_case(case_id, archived=archived)
