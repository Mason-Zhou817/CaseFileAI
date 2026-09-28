"""Versioned chunking and retrieval contracts."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol

SearchMode = Literal["bm25", "vector", "hybrid"]


class InvalidSearchError(ValueError):
    pass


class RetrievalIndexError(RuntimeError):
    pass


class EmbeddingProvider(Protocol):
    @property
    def model_version(self) -> str: ...

    def embed_documents(self, texts: list[str]) -> list[list[float]]: ...

    def embed_query(self, text: str) -> list[float]: ...


@dataclass(frozen=True, slots=True)
class ChunkDraft:
    chunk_id: str
    case_id: str
    page_id: str
    chunk_index: int
    text: str
    text_hash: str
    start_offset: int
    end_offset: int
    chunking_version: str


@dataclass(frozen=True, slots=True)
class IndexablePage:
    case_id: str
    document_id: str
    document_version_id: str
    page_id: str
    page_number: int
    text: str


@dataclass(frozen=True, slots=True)
class IndexResult:
    case_id: str
    pages_seen: int
    document_versions_seen: int
    chunks_total: int
    chunks_created: int
    embeddings_created: int
    chunking_version: str
    embedding_version: str


@dataclass(frozen=True, slots=True)
class RetrievalHit:
    chunk_id: str
    page_id: str
    document_id: str
    document_version_id: str
    page_number: int
    filename: str
    text: str
    score: float
    rank: int
    bm25_score: float | None
    bm25_rank: int | None
    vector_score: float | None
    vector_rank: int | None
    retrieval_sources: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class RankedChunk:
    chunk_id: str
    score: float
    rank: int


@dataclass(frozen=True, slots=True)
class ChunkSource:
    chunk_id: str
    page_id: str
    document_id: str
    document_version_id: str
    page_number: int
    filename: str
    text: str


class ChunkRepository(Protocol):
    def pages_for_case(self, case_id: str) -> list[IndexablePage]: ...

    def missing_chunks(self, chunk_ids: list[str]) -> set[str]: ...

    def missing_embeddings(self, chunk_ids: list[str], embedding_version: str) -> set[str]: ...

    def save_index(
        self,
        chunks: list[ChunkDraft],
        embeddings: dict[str, list[float]],
        embedding_version: str,
    ) -> tuple[int, int]: ...

    def search_bm25(
        self, case_id: str, query: str, chunking_version: str, limit: int
    ) -> list[RankedChunk]: ...

    def search_vectors(
        self,
        case_id: str,
        query_vector: list[float],
        chunking_version: str,
        embedding_version: str,
        limit: int,
    ) -> list[RankedChunk]: ...

    def hydrate(self, case_id: str, chunk_ids: list[str]) -> dict[str, ChunkSource]: ...


@dataclass(frozen=True, slots=True)
class RetrievalDependencies:
    repository: ChunkRepository
    embeddings: EmbeddingProvider
