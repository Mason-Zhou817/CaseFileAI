"""SQLite FTS5 and NumPy retrieval persistence."""

from __future__ import annotations

import numpy as np
from sqlalchemy import select, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session, sessionmaker

from casefile_ai.application.retrieval_contracts import (
    ChunkDraft,
    ChunkSource,
    IndexablePage,
    RankedChunk,
    RetrievalIndexError,
)
from casefile_ai.domain.exceptions import CaseNotFoundError
from casefile_ai.infrastructure.db.models import (
    CaseModel,
    ChunkEmbeddingModel,
    ChunkModel,
    DocumentModel,
    DocumentVersionModel,
    PageModel,
)
from casefile_ai.infrastructure.db.session import session_scope
from casefile_ai.infrastructure.retrieval.chinese_fts import (
    TOKENIZATION_VERSION,
    prepare_fts_text,
    prepare_match_query,
)


class SqliteChunkRepository:
    def __init__(self, factory: sessionmaker[Session]) -> None:
        self._factory = factory

    def _require_case(self, session: Session, case_id: str) -> None:
        if session.get(CaseModel, case_id) is None:
            raise CaseNotFoundError(case_id)

    def pages_for_case(self, case_id: str) -> list[IndexablePage]:
        with session_scope(self._factory) as session:
            self._require_case(session, case_id)
            rows = session.execute(
                select(PageModel, DocumentVersionModel, DocumentModel)
                .join(
                    DocumentVersionModel,
                    DocumentVersionModel.document_version_id == PageModel.document_version_id,
                )
                .join(DocumentModel, DocumentModel.document_id == DocumentVersionModel.document_id)
                .where(DocumentModel.case_id == case_id)
                .order_by(
                    DocumentModel.document_id,
                    DocumentVersionModel.version_number,
                    PageModel.page_number,
                )
            ).all()
            return [
                IndexablePage(
                    case_id=case_id,
                    document_id=document.document_id,
                    document_version_id=version.document_version_id,
                    page_id=page.page_id,
                    page_number=page.page_number,
                    text=page.text,
                )
                for page, version, document in rows
            ]

    def missing_chunks(self, chunk_ids: list[str]) -> set[str]:
        if not chunk_ids:
            return set()
        with session_scope(self._factory) as session:
            existing = set(
                session.scalars(
                    select(ChunkModel.chunk_id).where(ChunkModel.chunk_id.in_(chunk_ids))
                )
            )
        return set(chunk_ids) - existing

    def missing_embeddings(self, chunk_ids: list[str], embedding_version: str) -> set[str]:
        if not chunk_ids:
            return set()
        with session_scope(self._factory) as session:
            existing = set(
                session.scalars(
                    select(ChunkEmbeddingModel.chunk_id).where(
                        ChunkEmbeddingModel.chunk_id.in_(chunk_ids),
                        ChunkEmbeddingModel.embedding_version == embedding_version,
                    )
                )
            )
        return set(chunk_ids) - existing

    def save_index(
        self,
        chunks: list[ChunkDraft],
        embeddings: dict[str, list[float]],
        embedding_version: str,
    ) -> tuple[int, int]:
        chunks_created = 0
        embeddings_created = 0
        with session_scope(self._factory) as session:
            session.execute(text("BEGIN IMMEDIATE"))
            page_rows = session.execute(
                select(PageModel.page_id, PageModel.page_number).where(
                    PageModel.page_id.in_({chunk.page_id for chunk in chunks})
                )
            ).all()
            page_numbers = {str(row.page_id): int(row.page_number) for row in page_rows}
            for chunk in chunks:
                page_number = page_numbers.get(chunk.page_id)
                if page_number is None:
                    raise RetrievalIndexError("chunk page is unavailable")
                if session.get(ChunkModel, chunk.chunk_id) is None:
                    session.add(
                        ChunkModel(
                            chunk_id=chunk.chunk_id,
                            case_id=chunk.case_id,
                            page_id=chunk.page_id,
                            chunk_index=chunk.chunk_index,
                            text=chunk.text,
                            text_hash=chunk.text_hash,
                            start_offset=chunk.start_offset,
                            end_offset=chunk.end_offset,
                            chunking_version=chunk.chunking_version,
                        )
                    )
                    session.flush()
                    chunks_created += 1

                session.execute(
                    text("DELETE FROM chunk_fts WHERE chunk_id = :chunk_id"),
                    {"chunk_id": chunk.chunk_id},
                )
                session.execute(
                    text(
                        "INSERT INTO chunk_fts"
                        "(chunk_id, case_id, tokenization_version, search_text) "
                        "VALUES (:chunk_id, :case_id, :version, :search_text)"
                    ),
                    {
                        "chunk_id": chunk.chunk_id,
                        "case_id": chunk.case_id,
                        "version": TOKENIZATION_VERSION,
                        "search_text": prepare_fts_text(chunk.text, page_number=page_number),
                    },
                )

                vector = embeddings.get(chunk.chunk_id)
                if (
                    vector is not None
                    and session.get(ChunkEmbeddingModel, (chunk.chunk_id, embedding_version))
                    is None
                ):
                    values = np.asarray(vector, dtype="<f4")
                    if values.ndim != 1 or values.size == 0 or not np.isfinite(values).all():
                        raise RetrievalIndexError("invalid embedding vector")
                    session.add(
                        ChunkEmbeddingModel(
                            chunk_id=chunk.chunk_id,
                            embedding_version=embedding_version,
                            dimensions=int(values.size),
                            vector=values.tobytes(),
                        )
                    )
                    embeddings_created += 1
            session.flush()
        return chunks_created, embeddings_created

    def search_bm25(
        self, case_id: str, query: str, chunking_version: str, limit: int
    ) -> list[RankedChunk]:
        match_query = prepare_match_query(query)
        if not match_query:
            with session_scope(self._factory) as session:
                self._require_case(session, case_id)
            return []
        statement = text(
            "SELECT c.chunk_id, bm25(chunk_fts) AS raw_score "
            "FROM chunk_fts "
            "JOIN chunks c ON c.chunk_id = chunk_fts.chunk_id "
            "JOIN pages p ON p.page_id = c.page_id "
            "JOIN document_versions dv ON dv.document_version_id = p.document_version_id "
            "WHERE chunk_fts MATCH :query AND chunk_fts.case_id = :case_id "
            "AND chunk_fts.tokenization_version = :tokenization_version "
            "AND c.chunking_version = :chunking_version "
            "AND dv.version_number = ("
            "  SELECT MAX(current.version_number) FROM document_versions current "
            "  WHERE current.document_id = dv.document_id"
            ") ORDER BY raw_score, c.chunk_id LIMIT :limit"
        )
        try:
            with session_scope(self._factory) as session:
                self._require_case(session, case_id)
                rows = session.execute(
                    statement,
                    {
                        "query": match_query,
                        "case_id": case_id,
                        "tokenization_version": TOKENIZATION_VERSION,
                        "chunking_version": chunking_version,
                        "limit": limit,
                    },
                ).all()
        except OperationalError as exc:
            raise RetrievalIndexError("Chinese FTS5 query failed") from exc
        return [
            RankedChunk(str(row.chunk_id), -float(row.raw_score), rank)
            for rank, row in enumerate(rows, start=1)
        ]

    def search_vectors(
        self,
        case_id: str,
        query_vector: list[float],
        chunking_version: str,
        embedding_version: str,
        limit: int,
    ) -> list[RankedChunk]:
        query = np.asarray(query_vector, dtype=np.float32)
        if query.ndim != 1 or query.size == 0 or not np.isfinite(query).all():
            raise RetrievalIndexError("invalid query embedding")
        statement = text(
            "SELECT ce.chunk_id, ce.vector, ce.dimensions "
            "FROM chunk_embeddings ce "
            "JOIN chunks c ON c.chunk_id = ce.chunk_id "
            "JOIN pages p ON p.page_id = c.page_id "
            "JOIN document_versions dv ON dv.document_version_id = p.document_version_id "
            "WHERE c.case_id = :case_id AND c.chunking_version = :chunking_version "
            "AND ce.embedding_version = :embedding_version "
            "AND dv.version_number = ("
            "  SELECT MAX(current.version_number) FROM document_versions current "
            "  WHERE current.document_id = dv.document_id"
            ")"
        )
        with session_scope(self._factory) as session:
            self._require_case(session, case_id)
            rows = session.execute(
                statement,
                {
                    "case_id": case_id,
                    "chunking_version": chunking_version,
                    "embedding_version": embedding_version,
                },
            ).all()
        query_norm = float(np.linalg.norm(query))
        if query_norm == 0:
            raise RetrievalIndexError("zero query embedding")
        scored: list[tuple[str, float]] = []
        for row in rows:
            if int(row.dimensions) != query.size:
                raise RetrievalIndexError("stored embedding dimension mismatch")
            vector = np.frombuffer(row.vector, dtype="<f4")
            norm = float(np.linalg.norm(vector))
            if norm == 0:
                raise RetrievalIndexError("zero stored embedding")
            scored.append((str(row.chunk_id), float(np.dot(vector, query) / (norm * query_norm))))
        scored.sort(key=lambda item: (-item[1], item[0]))
        return [
            RankedChunk(chunk_id, score, rank)
            for rank, (chunk_id, score) in enumerate(scored[:limit], start=1)
        ]

    def hydrate(self, case_id: str, chunk_ids: list[str]) -> dict[str, ChunkSource]:
        if not chunk_ids:
            return {}
        with session_scope(self._factory) as session:
            rows = session.execute(
                select(ChunkModel, PageModel, DocumentVersionModel, DocumentModel)
                .join(PageModel, PageModel.page_id == ChunkModel.page_id)
                .join(
                    DocumentVersionModel,
                    DocumentVersionModel.document_version_id == PageModel.document_version_id,
                )
                .join(DocumentModel, DocumentModel.document_id == DocumentVersionModel.document_id)
                .where(ChunkModel.case_id == case_id, ChunkModel.chunk_id.in_(chunk_ids))
            ).all()
        return {
            chunk.chunk_id: ChunkSource(
                chunk_id=chunk.chunk_id,
                page_id=page.page_id,
                document_id=document.document_id,
                document_version_id=version.document_version_id,
                page_number=page.page_number,
                filename=version.filename,
                text=chunk.text,
            )
            for chunk, page, version, document in rows
        }
