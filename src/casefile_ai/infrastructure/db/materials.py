"""Version-scoped index facts and safe last-attempt persistence."""

import hashlib

from sqlalchemy import bindparam, select, text
from sqlalchemy.orm import Session, sessionmaker

from casefile_ai.application.material_contracts import VersionIndexFacts
from casefile_ai.application.retrieval_contracts import IndexablePage
from casefile_ai.application.services.chunking import CHUNKING_VERSION, chunk_page
from casefile_ai.infrastructure.db.documents import SqlAlchemyDocumentRepository
from casefile_ai.infrastructure.db.models import ChunkEmbeddingModel, ChunkModel, PageModel
from casefile_ai.infrastructure.db.session import session_scope
from casefile_ai.infrastructure.retrieval.chinese_fts import TOKENIZATION_VERSION


class SqlAlchemyMaterialRepository:
    def __init__(self, factory: sessionmaker[Session]) -> None:
        self._factory = factory
        self._documents = SqlAlchemyDocumentRepository(factory)

    def facts(self, case_id: str, embedding_version: str) -> list[VersionIndexFacts]:
        versions = self._documents.list_documents(case_id)
        result: list[VersionIndexFacts] = []
        with session_scope(self._factory) as session:
            for version in versions:
                signature = hashlib.sha256(
                    "|".join(
                        (
                            version.document_version_id,
                            version.content_sha256,
                            version.parser_version,
                            CHUNKING_VERSION,
                            embedding_version,
                            TOKENIZATION_VERSION,
                        )
                    ).encode()
                ).hexdigest()
                pages = list(
                    session.scalars(
                        select(PageModel).where(
                            PageModel.document_version_id == version.document_version_id
                        )
                    )
                )
                expected = {
                    chunk.chunk_id
                    for page in pages
                    for chunk in chunk_page(
                        IndexablePage(
                            case_id,
                            version.document_id,
                            version.document_version_id,
                            page.page_id,
                            page.page_number,
                            page.text,
                        )
                    )
                }
                chunks = list(
                    session.scalars(
                        select(ChunkModel).where(
                            ChunkModel.page_id.in_([page.page_id for page in pages])
                        )
                    )
                )
                current = {
                    chunk.chunk_id for chunk in chunks if chunk.chunking_version == CHUNKING_VERSION
                }
                embedded = set(
                    session.scalars(
                        select(ChunkEmbeddingModel.chunk_id).where(
                            ChunkEmbeddingModel.chunk_id.in_(expected),
                            ChunkEmbeddingModel.embedding_version == embedding_version,
                        )
                    )
                )
                fts: set[str] = set()
                if expected:
                    statement = text(
                        "SELECT chunk_id FROM chunk_fts WHERE chunk_id IN :ids "
                        "AND tokenization_version = :version"
                    ).bindparams(bindparam("ids", expanding=True))
                    fts = set(
                        session.scalars(
                            statement,
                            {
                                "ids": list(expected),
                                "version": TOKENIZATION_VERSION,
                            },
                        )
                    )
                attempt = session.execute(
                    text(
                        "SELECT outcome, diagnostic_id FROM material_index_attempts "
                        "WHERE document_version_id = :id AND signature = :signature"
                    ),
                    {"id": version.document_version_id, "signature": signature},
                ).first()
                complete = expected.issubset(current & embedded & fts)
                # Empty parsed text cannot distinguish import from index without an attempt.
                if not expected:
                    complete = attempt is not None and attempt.outcome == "ready"
                previous_attempt = session.scalar(
                    text("SELECT 1 FROM material_index_attempts WHERE document_version_id = :id"),
                    {"id": version.document_version_id},
                )
                result.append(
                    VersionIndexFacts(
                        version,
                        signature,
                        complete,
                        bool(chunks) or previous_attempt is not None,
                        str(attempt.outcome) if attempt else None,
                        str(attempt.diagnostic_id) if attempt and attempt.diagnostic_id else None,
                    )
                )
        return result

    def record_attempt(
        self, facts: list[VersionIndexFacts], outcome: str, diagnostic_id: str | None
    ) -> None:
        with session_scope(self._factory) as session:
            for item in facts:
                session.execute(
                    text(
                        "INSERT INTO material_index_attempts "
                        "(document_version_id, signature, outcome, diagnostic_id) "
                        "VALUES (:id, :signature, :outcome, :diagnostic) "
                        "ON CONFLICT(document_version_id, signature) DO UPDATE SET "
                        "outcome = excluded.outcome, diagnostic_id = excluded.diagnostic_id"
                    ),
                    {
                        "id": item.version.document_version_id,
                        "signature": item.signature,
                        "outcome": outcome,
                        "diagnostic": diagnostic_id,
                    },
                )
