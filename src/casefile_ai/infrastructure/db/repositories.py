"""SQLAlchemy repository implementations."""

from __future__ import annotations

import builtins

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from casefile_ai.application.contracts import CaseSummary
from casefile_ai.application.data_contracts import ManagedCase
from casefile_ai.domain.exceptions import CaseNotFoundError
from casefile_ai.infrastructure.db.models import CaseModel
from casefile_ai.infrastructure.db.session import session_scope


def _case_summary(model: CaseModel) -> CaseSummary:
    return CaseSummary(
        case_id=model.case_id,
        name=model.name,
        description=model.description,
    )


class SqlAlchemyCaseRepository:
    def __init__(self, factory: sessionmaker[Session]) -> None:
        self._factory = factory

    def add(self, case: CaseSummary) -> CaseSummary:
        with session_scope(self._factory) as session:
            model = CaseModel(
                case_id=case.case_id,
                name=case.name,
                description=case.description,
            )
            session.add(model)
        return case

    def list(self) -> list[CaseSummary]:
        return [
            CaseSummary(case.case_id, case.name, case.description)
            for case in self.managed_list(False)
        ]

    def get(self, case_id: str) -> CaseSummary | None:
        with session_scope(self._factory) as session:
            model = session.get(CaseModel, case_id)
            return _case_summary(model) if model is not None else None

    @staticmethod
    def _managed(model: CaseModel) -> ManagedCase:
        return ManagedCase(
            model.case_id,
            model.name,
            model.description,
            "archived" if model.status == "archived" else "active",
        )

    def managed_list(self, include_archived: bool) -> builtins.list[ManagedCase]:
        with session_scope(self._factory) as session:
            statement = select(CaseModel).order_by(CaseModel.created_at.desc(), CaseModel.case_id)
            if not include_archived:
                statement = statement.where(CaseModel.status == "active")
            return [self._managed(model) for model in session.scalars(statement)]

    def managed_get(self, case_id: str) -> ManagedCase:
        with session_scope(self._factory) as session:
            model = session.get(CaseModel, case_id)
            if model is None:
                raise CaseNotFoundError(case_id)
            return self._managed(model)

    def update_case(
        self, case_id: str, *, name: str | None = None, archived: bool | None = None
    ) -> ManagedCase:
        with session_scope(self._factory) as session:
            model = session.get(CaseModel, case_id)
            if model is None:
                raise CaseNotFoundError(case_id)
            if name is not None:
                model.name = name
            if archived is not None:
                model.status = "archived" if archived else "active"
            return self._managed(model)
