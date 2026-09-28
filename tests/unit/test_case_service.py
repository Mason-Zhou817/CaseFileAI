from casefile_ai.application.contracts import CaseSummary
from casefile_ai.application.services.cases import DefaultCaseService
from casefile_ai.domain.exceptions import CaseNotFoundError, InvalidCaseError


class MemoryCaseRepository:
    def __init__(self) -> None:
        self.items: dict[str, CaseSummary] = {}

    def add(self, case: CaseSummary) -> CaseSummary:
        self.items[case.case_id] = case
        return case

    def list(self) -> list[CaseSummary]:
        return list(self.items.values())

    def get(self, case_id: str) -> CaseSummary | None:
        return self.items.get(case_id)


def test_create_normalizes_case_fields() -> None:
    service = DefaultCaseService(MemoryCaseRepository())

    created = service.create_case("  合成案件  ", "  测试说明  ")

    assert created.name == "合成案件"
    assert created.description == "测试说明"
    assert service.get_case(created.case_id) == created


def test_blank_case_name_is_rejected() -> None:
    service = DefaultCaseService(MemoryCaseRepository())

    try:
        service.create_case("   ")
    except InvalidCaseError as error:
        assert "blank" in str(error)
    else:
        raise AssertionError("blank case name was accepted")


def test_missing_case_raises_typed_error() -> None:
    service = DefaultCaseService(MemoryCaseRepository())

    try:
        service.get_case("missing")
    except CaseNotFoundError as error:
        assert error.case_id == "missing"
    else:
        raise AssertionError("missing case did not raise")
