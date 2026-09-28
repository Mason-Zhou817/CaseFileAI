"""Indexing and source-traceable keyword/vector/hybrid retrieval."""

from __future__ import annotations

from collections.abc import Callable
from contextlib import AbstractContextManager, nullcontext

from casefile_ai.application.contracts import SearchHit
from casefile_ai.application.retrieval_contracts import (
    ChunkRepository,
    EmbeddingProvider,
    IndexResult,
    InvalidSearchError,
    RankedChunk,
    RetrievalHit,
    RetrievalIndexError,
    SearchMode,
)
from casefile_ai.application.services.chunking import CHUNKING_VERSION, chunk_page
from casefile_ai.domain.ids import derived_id

EMBEDDING_BATCH_SIZE = 64
RRF_K = 60
RETRIEVAL_VERSION = "hybrid-rrf-k60-page-token-v2"


class DefaultRetrievalService:
    def __init__(
        self,
        repository: ChunkRepository,
        embeddings: EmbeddingProvider,
        *,
        index_monitor: Callable[[str], AbstractContextManager[None]] | None = None,
    ) -> None:
        self._repository = repository
        self._embeddings = embeddings
        self._index_monitor = index_monitor

    @property
    def embedding_version(self) -> str:
        return self._embeddings.model_version

    @property
    def retrieval_version(self) -> str:
        return RETRIEVAL_VERSION

    def index_case(self, case_id: str) -> IndexResult:
        with self._index_monitor(case_id) if self._index_monitor else nullcontext():
            return self._index_case(case_id)

    def _index_case(self, case_id: str) -> IndexResult:
        pages = self._repository.pages_for_case(case_id)
        chunks = [chunk for page in pages for chunk in chunk_page(page)]
        chunk_ids = [chunk.chunk_id for chunk in chunks]
        missing_chunks = self._repository.missing_chunks(chunk_ids)
        missing_embeddings = self._repository.missing_embeddings(
            chunk_ids, self._embeddings.model_version
        )
        by_id = {chunk.chunk_id: chunk for chunk in chunks}
        vectors: dict[str, list[float]] = {}
        ordered_missing = [chunk_id for chunk_id in chunk_ids if chunk_id in missing_embeddings]
        for start in range(0, len(ordered_missing), EMBEDDING_BATCH_SIZE):
            batch_ids = ordered_missing[start : start + EMBEDDING_BATCH_SIZE]
            batch_vectors = self._embeddings.embed_documents(
                [by_id[item].text for item in batch_ids]
            )
            if len(batch_vectors) != len(batch_ids):
                raise RetrievalIndexError("embedding provider returned the wrong vector count")
            vectors.update(zip(batch_ids, batch_vectors, strict=True))
        chunks_created, embeddings_created = self._repository.save_index(
            chunks, vectors, self._embeddings.model_version
        )
        if chunks_created > len(missing_chunks):
            raise RetrievalIndexError("repository reported inconsistent chunk count")
        return IndexResult(
            case_id=case_id,
            pages_seen=len(pages),
            document_versions_seen=len({page.document_version_id for page in pages}),
            chunks_total=len(chunks),
            chunks_created=chunks_created,
            embeddings_created=embeddings_created,
            chunking_version=CHUNKING_VERSION,
            embedding_version=self._embeddings.model_version,
        )

    def search(
        self,
        case_id: str,
        query: str,
        mode: SearchMode = "hybrid",
        limit: int = 10,
    ) -> list[RetrievalHit]:
        query = query.strip()
        if not query:
            raise InvalidSearchError("query must not be blank")
        if mode not in {"bm25", "vector", "hybrid"}:
            raise InvalidSearchError("mode must be bm25, vector, or hybrid")
        if not 1 <= limit <= 50:
            raise InvalidSearchError("limit must be between 1 and 50")
        candidate_limit = min(200, max(20, limit * 4))
        bm25 = (
            self._repository.search_bm25(case_id, query, CHUNKING_VERSION, candidate_limit)
            if mode in {"bm25", "hybrid"}
            else []
        )
        vector = (
            self._repository.search_vectors(
                case_id,
                self._embeddings.embed_query(query),
                CHUNKING_VERSION,
                self._embeddings.model_version,
                candidate_limit,
            )
            if mode in {"vector", "hybrid"}
            else []
        )
        ordered = self._rank(mode, bm25, vector)[:limit]
        sources = self._repository.hydrate(case_id, [item[0] for item in ordered])
        bm25_by_id = {item.chunk_id: item for item in bm25}
        vector_by_id = {item.chunk_id: item for item in vector}
        hits: list[RetrievalHit] = []
        for rank, (chunk_id, score) in enumerate(ordered, start=1):
            source = sources.get(chunk_id)
            if source is None:
                raise RetrievalIndexError("ranked chunk source is unavailable")
            keyword = bm25_by_id.get(chunk_id)
            semantic = vector_by_id.get(chunk_id)
            hits.append(
                RetrievalHit(
                    chunk_id=chunk_id,
                    page_id=source.page_id,
                    document_id=source.document_id,
                    document_version_id=source.document_version_id,
                    page_number=source.page_number,
                    filename=source.filename,
                    text=source.text,
                    score=score,
                    rank=rank,
                    bm25_score=keyword.score if keyword else None,
                    bm25_rank=keyword.rank if keyword else None,
                    vector_score=semantic.score if semantic else None,
                    vector_rank=semantic.rank if semantic else None,
                    retrieval_sources=tuple(
                        name
                        for name, present in (("bm25", keyword), ("vector", semantic))
                        if present is not None
                    ),
                )
            )
        return hits

    @staticmethod
    def _rank(
        mode: SearchMode, bm25: list[RankedChunk], vector: list[RankedChunk]
    ) -> list[tuple[str, float]]:
        if mode == "bm25":
            return [(item.chunk_id, item.score) for item in bm25]
        if mode == "vector":
            return [(item.chunk_id, item.score) for item in vector]
        fused: dict[str, float] = {}
        for ranking in (bm25, vector):
            for item in ranking:
                fused[item.chunk_id] = fused.get(item.chunk_id, 0.0) + 1.0 / (RRF_K + item.rank)
        return sorted(fused.items(), key=lambda item: (-item[1], item[0]))

    def search_case_evidence(self, case_id: str, query: str, limit: int = 10) -> list[SearchHit]:
        return [
            SearchHit(
                evidence_id=derived_id("evidence", hit.chunk_id),
                chunk_id=hit.chunk_id,
                document_id=hit.document_id,
                document_version_id=hit.document_version_id,
                page_id=hit.page_id,
                filename=hit.filename,
                page_number=hit.page_number,
                quote=hit.text,
                score=hit.score,
                rank=hit.rank,
                bm25_score=hit.bm25_score,
                bm25_rank=hit.bm25_rank,
                vector_score=hit.vector_score,
                vector_rank=hit.vector_rank,
                retrieval_sources=hit.retrieval_sources,
            )
            for hit in self.search(case_id, query, "hybrid", limit)
        ]
