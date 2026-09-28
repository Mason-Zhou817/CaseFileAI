"""Atomic SQLite document/version/page repository."""

from dataclasses import replace

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session, sessionmaker

from casefile_ai.application.document_contracts import (
    DocumentNotFoundError,
    DocumentPage,
    DocumentVersion,
    ParsedPage,
)
from casefile_ai.domain.exceptions import CaseNotFoundError
from casefile_ai.domain.ids import new_id
from casefile_ai.infrastructure.db.models import (
    CaseModel,
    DocumentModel,
    DocumentVersionModel,
    PageModel,
)
from casefile_ai.infrastructure.db.session import session_scope


def summary(case_id: str, row: DocumentVersionModel) -> DocumentVersion:
    return DocumentVersion(
        case_id,
        row.document_id,
        row.document_version_id,
        row.filename,
        row.content_sha256,
        row.page_count,
        row.version_number,
        row.parser_version,
    )


class SqlAlchemyDocumentRepository:
    def __init__(self, factory: sessionmaker[Session]) -> None:
        self._factory = factory

    def _check(self, session: Session, case_id: str, document_id: str | None) -> None:
        if session.get(CaseModel, case_id) is None:
            raise CaseNotFoundError(case_id)
        if document_id is not None:
            document = session.get(DocumentModel, document_id)
            if document is None or document.case_id != case_id:
                raise DocumentNotFoundError("document not found in case")

    def _duplicate(
        self,
        session: Session,
        case_id: str,
        sha256: str,
        document_id: str | None,
    ) -> DocumentVersion | None:
        query = (
            select(DocumentVersionModel)
            .join(DocumentModel)
            .where(
                DocumentModel.case_id == case_id,
                DocumentVersionModel.content_sha256 == sha256,
            )
        )
        if document_id is not None:
            query = query.where(DocumentVersionModel.document_id == document_id)
        row = session.scalars(query.order_by(DocumentVersionModel.document_version_id)).first()
        return replace(summary(case_id, row), duplicate=True) if row is not None else None

    def find_duplicate(
        self,
        case_id: str,
        sha256: str,
        document_id: str | None,
    ) -> DocumentVersion | None:
        with session_scope(self._factory) as session:
            self._check(session, case_id, document_id)
            return self._duplicate(session, case_id, sha256, document_id)

    def save(
        self,
        case_id: str,
        document_id: str | None,
        version_id: str,
        filename: str,
        sha256: str,
        storage_key: str,
        parser_version: str,
        pages: list[ParsedPage],
    ) -> DocumentVersion:
        with session_scope(self._factory) as session:
            # Serialize duplicate check + version allocation, including across processes.
            session.execute(text("BEGIN IMMEDIATE"))
            self._check(session, case_id, document_id)
            duplicate = self._duplicate(session, case_id, sha256, document_id)
            if duplicate is not None:
                return duplicate
            if document_id is None:
                document_id = new_id()
                session.add(DocumentModel(document_id=document_id, case_id=case_id))
                session.flush()
            latest = (
                session.scalar(
                    select(func.max(DocumentVersionModel.version_number)).where(
                        DocumentVersionModel.document_id == document_id
                    )
                )
                or 0
            )
            row = DocumentVersionModel(
                document_version_id=version_id,
                document_id=document_id,
                version_number=latest + 1,
                filename=filename,
                content_sha256=sha256,
                storage_key=storage_key,
                page_count=len(pages),
                parser_version=parser_version,
            )
            session.add(row)
            session.flush()
            for page in pages:
                session.add(
                    PageModel(
                        page_id=page.page_id,
                        document_version_id=version_id,
                        page_number=page.page_number,
                        text=page.text,
                        text_hash=page.text_hash,
                        ocr_used=page.ocr_used,
                    )
                )
            session.flush()
            return summary(case_id, row)

    def list_documents(self, case_id: str) -> list[DocumentVersion]:
        with session_scope(self._factory) as session:
            self._check(session, case_id, None)
            rows = session.scalars(
                select(DocumentVersionModel)
                .join(DocumentModel)
                .where(DocumentModel.case_id == case_id)
                .order_by(
                    DocumentVersionModel.document_id, DocumentVersionModel.version_number.desc()
                )
            )
            latest: dict[str, DocumentVersion] = {}
            for row in rows:
                latest.setdefault(row.document_id, summary(case_id, row))
            return list(latest.values())

    def versions(self, document_id: str) -> list[DocumentVersion]:
        with session_scope(self._factory) as session:
            document = session.get(DocumentModel, document_id)
            if document is None:
                raise DocumentNotFoundError("document not found")
            return [
                summary(document.case_id, row)
                for row in session.scalars(
                    select(DocumentVersionModel)
                    .where(DocumentVersionModel.document_id == document_id)
                    .order_by(DocumentVersionModel.version_number.desc())
                )
            ]

    def resolve(self, document_id: str, version_id: str | None) -> DocumentVersion:
        versions = self.versions(document_id)
        for version in versions:
            if version_id is None or version.document_version_id == version_id:
                return version
        raise DocumentNotFoundError("document version not found")

    def page(self, document_id: str, number: int, version_id: str | None) -> DocumentPage:
        version = self.resolve(document_id, version_id)
        with session_scope(self._factory) as session:
            row = session.scalar(
                select(PageModel).where(
                    PageModel.document_version_id == version.document_version_id,
                    PageModel.page_number == number,
                )
            )
            if row is None:
                raise DocumentNotFoundError("page not found")
            return DocumentPage(
                version.case_id,
                document_id,
                version.document_version_id,
                row.page_id,
                row.page_number,
                version.filename,
                row.text,
                row.text_hash,
                row.ocr_used,
            )

    def storage_key(self, document_id: str, version_id: str | None) -> tuple[str, str]:
        version = self.resolve(document_id, version_id)
        with session_scope(self._factory) as session:
            row = session.get(DocumentVersionModel, version.document_version_id)
            assert row is not None
            return row.storage_key, row.filename
