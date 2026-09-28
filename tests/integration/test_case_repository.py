from pathlib import Path

from casefile_ai.application.services.cases import DefaultCaseService
from casefile_ai.infrastructure.db.repositories import SqlAlchemyCaseRepository
from casefile_ai.infrastructure.db.session import (
    build_engine,
    build_session_factory,
    initialize_schema,
)


def test_case_round_trip_uses_sqlite(tmp_path: Path) -> None:
    engine = build_engine(f"sqlite:///{(tmp_path / 'cases.db').as_posix()}")
    initialize_schema(engine)
    service = DefaultCaseService(SqlAlchemyCaseRepository(build_session_factory(engine)))

    first = service.create_case("案件一")
    second = service.create_case("案件二", "合成数据")

    assert service.get_case(first.case_id) == first
    assert {case.case_id for case in service.list_cases()} == {first.case_id, second.case_id}
    engine.dispose()
